"""Point-in-time CSI 500 factor scoring and constrained enhancement pipeline.

The pipeline is deliberately strict:

* CSI500 membership and index weights are exact signal-date observations;
* rolling factor evidence uses labels whose maturity date is strictly earlier
  than the current signal date;
* a period without sufficient causal evidence is warm-up/blocked, never filled
  by a fixed score;
* only certified weights returned by :mod:`stock_constraint_optimizer` are
  treated as new orders; and
* the direct-score portfolio is an independent, tradable comparison on the
  same frozen 50-name support, never an optimizer fallback.

No function in this module writes files.  Optional score persistence writes to
the caller-supplied SQLite connection only.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

try:
    from .stock_constraint_optimizer import (
        StockOptimizerConfig,
        build_psd_factor_risk_root,
        optimize_stock_portfolio,
    )
    from .timing_overlay import (
        TimingOverlayConfig,
        apply_alpha_overlay,
        apply_timing_budget_to_optimizer_config,
        build_timing_overlay,
    )
except ImportError:  # pragma: no cover - direct script/import compatibility.
    from model.portfolio_optimization.stock_constraint_optimizer import (
        StockOptimizerConfig,
        build_psd_factor_risk_root,
        optimize_stock_portfolio,
    )
    from model.portfolio_optimization.timing_overlay import (
        TimingOverlayConfig,
        apply_alpha_overlay,
        apply_timing_budget_to_optimizer_config,
        build_timing_overlay,
    )

from model.factor_laboratory.adaptive_icir import symmetric_orthogonalize


SCHEMA_VERSION = "csi500-strategy/1.4-beta-anchored-timing-optimizer"
SCORE_TABLE = "optimizer_factor_score_period"
DEFAULT_FACTORS = (
    "quality_value_low_crowding_v8",
    "fundamental_quality_v4",
    "index_industry_risk_alpha_v8",
    "factor_domain_agent_v9",
    "deep_factor_agent_v4",
    "ai_factor_blend_v5",
    "small_value_quality_momo",
    "portfolio_optimizer_score_v2",
)

# Exact factor set used by the previously validated walk-forward IC v10
# champion in framework.backtest.run_v2_models.WALKFORWARD_IC_FACTORS.
# Do not expand this list under the same model name: any addition creates a
# different signal and must enter the challenger/governance workflow.
WALKFORWARD_CHAMPION_FACTORS = (
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

BAYESIAN_RESPONSIVE_SATELLITE_SCORE = "bayesian_responsive_satellite_v17_score"
BAYESIAN_RESPONSIVE_SATELLITE_CONFIDENCE = "bayesian_responsive_satellite_v17_confidence"
BAYESIAN_RESPONSIVE_FACTORS = (
    *WALKFORWARD_CHAMPION_FACTORS,
    BAYESIAN_RESPONSIVE_SATELLITE_SCORE,
)

FACTOR_LAB_BRIDGE_FEATURES = (
    "ret_1",
    "ret_5",
    "ret_20",
    "ret_60",
    "vol_20",
    "down_vol_20",
    "price_pos_60",
    "volume_z_20",
    "amihud_20",
    "turnover",
    "volume_ratio",
    "value_ep",
    "value_bp",
    "value_sp",
    "dividend",
    "log_mv",
    "moneyflow",
    "large_flow",
    "extreme_flow",
    "range_1",
    "gap_1",
    "quality_roe",
    "quality_roa",
    "quality_gross_margin",
    "quality_asset_turn",
    "quality_low_leverage",
    "growth_revenue",
    "growth_operating_profit",
    "growth_net_profit",
)

FACTOR_LAB_LEGACY_FEATURES = FACTOR_LAB_BRIDGE_FEATURES[:21]

AMOUNT_UNIT_TO_CNY_MULTIPLIER = {
    "cny": 1.0,
    "thousand_cny": 1000.0,
}



class CSI500DataContractError(ValueError):
    """Raised when a point-in-time input cannot support a certified run."""


@dataclass(frozen=True)
class CSI500StrategyConfig:
    universe: str = "CSI500_ENH"
    index_code: str = "000905.SH"
    expected_members: int = 500
    minimum_members: int = 450
    require_exact_members: bool = True
    raw_index_weight_total: float = 100.0
    raw_index_weight_tolerance: float = 2.0
    normalized_weight_tolerance: float = 1.0e-8
    factor_columns: tuple[str, ...] = DEFAULT_FACTORS
    factor_lookback: int = 48
    factor_min_history: int = 12
    minimum_ic_cross_section: int = 100
    factor_min_coverage: float = 0.98
    factor_ic_mean_prior_std: float = 0.05
    factor_ic_covariance_shrinkage: float = 0.50
    factor_ic_covariance_ridge: float = 1.0e-6
    factor_absolute_weight_cap: float = 0.25
    factor_weight_method: str = "precision"
    walkforward_positive_ic_min_hit_rate: float = 0.45
    factor_evidence_prior_observations: float = 6.0
    factor_evidence_clip: float = 3.0
    missing_member_max_quote_staleness_trading_days: int = 22
    benchmark_mtm_max_exit_staleness_trading_days: int = 22
    missing_member_max_factor_staleness_periods: int = 1
    allow_no_alpha_view_missing_members: bool = False
    require_official_index_benchmark: bool = False
    official_index_database_path: str = ""
    style_columns: tuple[str, ...] = (
        "style_size",
        "style_value",
        "style_momentum",
        "style_liquidity",
        "style_beta",
    )
    optimizer_style_columns: tuple[str, ...] = (
        "style_size",
        "style_value",
        "style_momentum",
        "style_liquidity",
    )
    style_min_coverage: float = 0.95
    beta_exposure_enabled: bool = True
    beta_lookback_trading_days: int = 252
    beta_min_trading_observations: int = 80
    beta_lookback_periods: int = 36
    beta_min_period_observations: int = 12
    beta_prior_observations: float = 60.0
    beta_prior_value: float = 1.0
    beta_clip_bounds: tuple[float, float] = (-1.0, 3.0)
    neutralization_ridge: float = 1.0e-8
    neutralization_tolerance: float = 5.0e-7
    risk_lookback: int = 60
    risk_min_observations: int = 20
    risk_min_asset_coverage: float = 0.75
    database_risk_lookback_trading_days: int = 504
    database_risk_min_return_observations: int = 120
    database_risk_min_asset_return_observations: int = 20
    database_risk_max_common_return_observations: int = 252
    database_risk_min_row_coverage: float = 0.90
    database_risk_half_life: float = 63.0
    database_risk_base_diagonal_shrinkage: float = 0.30
    database_risk_max_diagonal_shrinkage: float = 0.85
    database_risk_specific_variance_prior_observations: float = 60.0
    database_risk_allow_ipo_specific_prior: bool = False
    database_risk_max_abs_daily_return: float = 0.35
    portfolio_notional: float = 100_000_000.0
    max_adv_participation: float = 0.10
    trading_days_per_period: int = 1
    liquidity_amount_unit: str = "thousand_cny"
    liquidity_amount_to_cny_multiplier: float = 1000.0
    default_trade_limit: float = 1.0
    transaction_cost_rate: float = 0.001
    periods_per_year: int = 12
    score_name: str = "causal_adaptive_bayesian_icir_neutral_score"
    score_source_mode: str = "causal_recompute"
    precomputed_score_name: str | None = None
    precomputed_score_run_id: str | None = None
    precomputed_score_min_coverage: float = 0.98
    factor_lab_profile: str = "high_sharpe_enhanced"
    factor_lab_bridge_include_warehouse_factors: bool = True
    factor_lab_bridge_max_factor_candidates: int = 180
    factor_lab_bridge_screen_top_n: int = 60
    factor_lab_bridge_screen_lookback_days: int = 252
    factor_lab_bridge_screen_rebalance_days: int = 63
    factor_lab_bridge_screen_min_coverage: float = 0.35
    factor_lab_bridge_screen_min_dates: int = 12
    factor_lab_bridge_screen_min_assets_per_date: int = 100
    factor_lab_bridge_screen_max_pair_corr: float = 0.92
    factor_lab_bridge_external_factor_max_staleness_days: int = 63
    factor_lab_bridge_lookback_periods: int = 48
    factor_lab_bridge_min_periods: int = 12
    factor_lab_bridge_min_training_periods: int = 12
    factor_lab_bridge_min_training_rows: int = 3000
    factor_lab_bridge_alpha_neutralization: bool = True
    train_end: str = "20221231"
    validation_end: str = "20231231"
    formal_evaluation_start: str | None = None
    test_role: str = "report_only"
    timing_overlay: TimingOverlayConfig = field(
        default_factory=TimingOverlayConfig
    )
    optimizer: StockOptimizerConfig = field(
        default_factory=lambda: StockOptimizerConfig(target_holdings=50)
    )


RiskProvider = Callable[[str, pd.DataFrame, pd.DataFrame], Any]
HoldingValuationProvider = Callable[
    [str, str, Sequence[str]], Mapping[str, Any]
]

class DatabasePointInTimeRiskProvider:
    """Cached point-in-time daily risk data sourced from local qfq prices.

    Prices are prefetched once for the union of assets requested by the
    database strategy.  Each call then takes only market dates strictly before
    its signal date.  Missing prices are never zero-filled or backfilled.
    Forward filling is allowed only on a row explicitly marked suspended, and
    that policy is exposed in the period audit.
    """

    REQUIRED_PRICE_COLUMNS = {
        "trade_date", "ts_code", "qfq_close", "suspend_timing",
    }
    REQUIRED_CALENDAR_COLUMNS = {"trade_date", "is_trade_day", "source"}

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        asset_codes: Sequence[str],
        signal_dates: Sequence[str],
        config: CSI500StrategyConfig,
    ) -> None:
        _validate_config(config)
        codes = sorted({str(code).strip() for code in asset_codes if str(code).strip()})
        dates = sorted({_date_text(value) for value in signal_dates})
        if not codes:
            raise CSI500DataContractError("database_risk_asset_codes_empty")
        if not dates:
            raise CSI500DataContractError("database_risk_signal_dates_empty")
        columns = {
            str(row[1]) for row in connection.execute(
                "pragma table_info(stock_ohlcv_daily)"
            ).fetchall()
        }
        missing_columns = sorted(self.REQUIRED_PRICE_COLUMNS - columns)
        if missing_columns:
            raise CSI500DataContractError(
                "stock_ohlcv_daily_missing_risk_columns:"
                + ",".join(missing_columns)
            )

        calendar_columns = {
            str(row[1]) for row in connection.execute(
                "pragma table_info(trade_calendar)"
            ).fetchall()
        }
        missing_calendar_columns = sorted(
            self.REQUIRED_CALENDAR_COLUMNS - calendar_columns
        )
        if missing_calendar_columns:
            raise CSI500DataContractError(
                "trade_calendar_missing_risk_columns:"
                + ",".join(missing_calendar_columns)
            )
        invalid_calendar_flags = int(connection.execute(
            """
            select count(*)
            from trade_calendar
            where is_trade_day is null
               or cast(is_trade_day as text) not in ('0', '1')
            """
        ).fetchone()[0])
        if invalid_calendar_flags:
            raise CSI500DataContractError(
                f"trade_calendar_invalid_is_trade_day_rows:{invalid_calendar_flags}"
            )
        earliest_signal = dates[0]
        latest_signal = dates[-1]
        calendar_start_row = connection.execute(
            """
            select min(trade_date)
            from (
              select trade_date
              from trade_calendar
              where trade_date < ?
              group by trade_date
              having max(cast(is_trade_day as integer)) = 1
              order by trade_date desc
              limit ?
            )
            """,
            (
                earliest_signal,
                int(config.database_risk_lookback_trading_days) + 1,
            ),
        ).fetchone()
        calendar_start = (
            _date_text(calendar_start_row[0])
            if calendar_start_row is not None and calendar_start_row[0] is not None
            else earliest_signal
        )
        calendar_source_profile = _trade_calendar_source_profile(
            connection,
            start=calendar_start,
            end=latest_signal,
            context="trade_calendar",
        )
        calendar_rows = connection.execute(
            """
            select trade_date,
                   max(cast(is_trade_day as integer)) as is_trade_day
            from trade_calendar
            where trade_date >= ? and trade_date < ?
            group by trade_date
            having max(cast(is_trade_day as integer)) = 1
            order by trade_date
            """,
            (calendar_start, latest_signal),
        ).fetchall()
        calendar = [_date_text(row[0]) for row in calendar_rows]
        if not calendar:
            raise CSI500DataContractError("database_risk_market_calendar_empty")
        calendar_sources = list(calendar_source_profile["sources"])
        duplicate_calendar_rows = int(connection.execute(
            """
            select count(*) - count(distinct trade_date)
            from trade_calendar
            where trade_date >= ? and trade_date < ?
            """,
            (calendar_start, latest_signal),
        ).fetchone()[0])
        conflicting_calendar_dates = int(connection.execute(
            """
            select count(*)
            from (
              select trade_date
              from trade_calendar
              where trade_date >= ? and trade_date < ?
              group by trade_date
              having min(cast(is_trade_day as integer))
                   <> max(cast(is_trade_day as integer))
            )
            """,
            (calendar_start, latest_signal),
        ).fetchone()[0])
        nontrading_calendar_dates_excluded = int(connection.execute(
            """
            select count(*)
            from (
              select trade_date
              from trade_calendar
              where trade_date >= ? and trade_date < ?
              group by trade_date
              having max(cast(is_trade_day as integer)) = 0
            )
            """,
            (calendar_start, latest_signal),
        ).fetchone()[0])

        price_rows: list[tuple[Any, ...]] = []
        chunk_size = 400
        chunks = 0
        for offset in range(0, len(codes), chunk_size):
            chunk = codes[offset:offset + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            query = (
                "select trade_date, ts_code, qfq_close, suspend_timing "
                "from stock_ohlcv_daily "
                "where trade_date >= ? and trade_date < ? "
                f"and ts_code in ({placeholders}) "
                "order by trade_date, ts_code"
            )
            price_rows.extend(
                connection.execute(
                    query, (calendar_start, latest_signal, *chunk)
                ).fetchall()
            )
            chunks += 1
        prices = pd.DataFrame(
            price_rows,
            columns=["trade_date", "ts_code", "qfq_close", "suspend_timing"],
        )
        if prices.empty:
            raise CSI500DataContractError("database_risk_price_rows_empty")
        prices["trade_date"] = prices["trade_date"].map(_date_text)
        prices["ts_code"] = prices["ts_code"].astype(str).str.strip()
        if prices.duplicated(["trade_date", "ts_code"]).any():
            raise CSI500DataContractError("database_risk_duplicate_price_rows")
        prices["qfq_close"] = pd.to_numeric(prices["qfq_close"], errors="coerce")

        self._config = config
        self._asset_codes = tuple(codes)
        self._signal_dates = tuple(dates)
        self._calendar = tuple(calendar)
        self._prices = prices
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self.prefetch_audit = {
            "provider": "database_point_in_time_qfq_factor_risk",
            "source_table": "stock_ohlcv_daily",
            "calendar_table": "trade_calendar",
            "calendar_source": calendar_sources,
            "calendar_open_rule": "group_by_trade_date_max_is_trade_day_equals_one",
            "calendar_date_aggregation": "deterministic_max_is_trade_day",
            "calendar_duplicate_rows": duplicate_calendar_rows,
            "calendar_conflicting_open_flags": conflicting_calendar_dates,
            "calendar_nontrading_dates_excluded": nontrading_calendar_dates_excluded,
            "price_field": "qfq_close",
            "prefetched": True,
            "cache_enabled": True,
            "asset_union_count": len(codes),
            "signal_period_count": len(dates),
            "calendar_start": calendar[0],
            "calendar_end_exclusive": latest_signal,
            "market_calendar_rows": len(calendar),
            "price_rows": int(len(prices)),
            "sql_asset_chunks": chunks,
            "future_rows_loaded": 0,
            "writes_performed": 0,
        }
        self.prefetch_audit.update(calendar_source_profile["audit"])

    @staticmethod
    def _explicit_suspension(value: Any) -> bool:
        if value is None or pd.isna(value):
            return False
        text = str(value).strip().lower()
        return text not in {
            "", "0", "0.0", "false", "none", "nan", "normal",
            "\u6b63\u5e38", "\u6b63\u5e38\u4ea4\u6613",
            "\u672a\u505c\u724c",
        }

    def __call__(
        self,
        signal_date: str,
        group: pd.DataFrame,
        history: pd.DataFrame,
    ) -> dict[str, Any]:
        del history  # Daily database history is authoritative for this provider.
        signal = _date_text(signal_date)
        if signal not in self._signal_dates:
            raise CSI500DataContractError(
                f"database_risk_unregistered_signal_date:{signal}"
            )
        codes = group["ts_code"].astype(str).str.strip().tolist()
        if len(codes) != len(set(codes)) or any(not code for code in codes):
            raise CSI500DataContractError("database_risk_codes_must_be_unique_nonempty")
        missing_prefetch = sorted(set(codes) - set(self._asset_codes))
        if missing_prefetch:
            raise CSI500DataContractError(
                "database_risk_assets_not_prefetched:"
                + ",".join(missing_prefetch[:20])
            )
        relevant_columns = ["ts_code", "industry", *self._config.style_columns]
        cache_key = (
            signal,
            _hash_parts(group[relevant_columns].sort_values("ts_code", kind="mergesort")),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return {
                **cached,
                "risk_root": cached["risk_root"].copy(),
                "diagnostics": {
                    **cached["diagnostics"],
                    "cache_hit": True,
                },
            }

        calendar = [
            value for value in self._calendar if value < signal
        ][-(int(self._config.database_risk_lookback_trading_days) + 1):]
        required_price_dates = (
            int(self._config.database_risk_min_return_observations) + 1
        )
        if len(calendar) < required_price_dates:
            raise CSI500DataContractError(
                "database_risk_market_history_below_minimum:"
                f"{len(calendar)}<{required_price_dates}"
            )
        raw = self._prices[
            (self._prices["trade_date"].isin(calendar))
            & (self._prices["ts_code"].isin(codes))
        ].copy()
        raw_prices = raw.pivot(
            index="trade_date", columns="ts_code", values="qfq_close"
        ).reindex(index=calendar, columns=codes)
        raw_suspend = raw.assign(
            explicitly_suspended=raw["suspend_timing"].map(
                self._explicit_suspension
            )
        ).pivot(
            index="trade_date", columns="ts_code",
            values="explicitly_suspended",
        ).reindex(index=calendar, columns=codes).eq(True)

        invalid_price_mask = raw_prices.notna() & (
            (~np.isfinite(raw_prices)) | (raw_prices <= 0.0)
        )
        prices = raw_prices.mask(invalid_price_mask)
        suspension_fill_count = 0
        for code in codes:
            last_valid: float | None = None
            for trade_date in calendar:
                value = prices.at[trade_date, code]
                if pd.notna(value) and math.isfinite(float(value)):
                    last_valid = float(value)
                    continue
                if bool(raw_suspend.at[trade_date, code]) and last_valid is not None:
                    prices.at[trade_date, code] = last_valid
                    suspension_fill_count += 1
                else:
                    # Do not carry prices across an unaudited data gap.
                    last_valid = None

        all_returns = prices.pct_change(fill_method=None).iloc[1:]
        all_returns.index.name = "return_end_date"
        if not all_returns.empty and str(all_returns.index.max()) >= signal:
            raise CSI500DataContractError(
                "database_risk_future_or_signal_date_return_detected"
            )

        first_valid_dates: dict[str, str] = {}
        for code in codes:
            first = all_returns[code].first_valid_index()
            if first is not None:
                first_valid_dates[code] = str(first)

        maximum_window = int(
            self._config.database_risk_max_common_return_observations
        )
        returns = all_returns.tail(maximum_window).copy()
        minimum_factor_observations = int(
            self._config.database_risk_min_return_observations
        )
        if len(returns) < minimum_factor_observations:
            raise CSI500DataContractError(
                "database_risk_factor_window_below_minimum:"
                f"{len(returns)}<{minimum_factor_observations}"
            )
        minimum_asset_observations = int(
            self._config.database_risk_min_asset_return_observations
        )
        return_counts = returns.notna().sum(axis=0)
        below_asset_floor = return_counts[
            return_counts < minimum_asset_observations
        ]
        if (
            not below_asset_floor.empty
            and not self._config.database_risk_allow_ipo_specific_prior
        ):
            details = ",".join(
                f"{code}:{int(value)}"
                for code, value in below_asset_floor.sort_values(
                    kind="mergesort"
                ).items()
            )
            raise CSI500DataContractError(
                "database_risk_asset_return_observations_below_minimum:"
                + details[:1200]
            )
        extreme = returns.abs() > float(
            self._config.database_risk_max_abs_daily_return
        )
        if extreme.any().any():
            locations = np.argwhere(extreme.to_numpy())
            examples = [
                f"{returns.columns[column]}@{returns.index[row]}"
                for row, column in locations[:12]
            ]
            raise CSI500DataContractError(
                "database_risk_daily_return_exceeds_a_share_guard:"
                + ",".join(examples)
            )

        asset_coverage = returns.notna().mean(axis=0)
        row_coverage = returns.notna().mean(axis=1)
        if row_coverage.empty or float(row_coverage.min()) < float(
            self._config.database_risk_min_row_coverage
        ):
            minimum = float(row_coverage.min()) if len(row_coverage) else 0.0
            worst_dates = [] if row_coverage.empty else [
                str(value) for value in row_coverage.nsmallest(5).index
            ]
            raise CSI500DataContractError(
                f"database_risk_row_coverage_below_threshold:{minimum:.6f};"
                + "dates=" + ",".join(worst_dates)
            )

        sample_length_ratio = min(
            1.0, len(returns) / float(maximum_window)
        )
        base_shrinkage = float(
            self._config.database_risk_base_diagonal_shrinkage
        )
        maximum_shrinkage = float(
            self._config.database_risk_max_diagonal_shrinkage
        )
        effective_shrinkage = (
            base_shrinkage
            + (maximum_shrinkage - base_shrinkage)
            * (1.0 - sample_length_ratio)
        )
        latest_first_valid = (
            max(first_valid_dates.values()) if first_valid_dates else ""
        )
        latest_entry_assets = sorted(
            code for code, date in first_valid_dates.items()
            if date == latest_first_valid
        )

        ordered_group = group[
            ["ts_code", "industry", *self._config.style_columns]
        ].copy()
        try:
            built = build_psd_factor_risk_root(
                returns,
                ordered_group[["ts_code", "industry"]],
                style_exposures=ordered_group[
                    ["ts_code", *self._config.style_columns]
                ],
                signal_date=signal,
                annualization=252.0,
                diagonal_shrinkage=effective_shrinkage,
                half_life=float(self._config.database_risk_half_life),
                minimum_row_coverage=float(
                    self._config.database_risk_min_row_coverage
                ),
                minimum_asset_coverage=float(
                    self._config.risk_min_asset_coverage
                ),
                minimum_factor_observations=minimum_factor_observations,
                minimum_asset_observations=minimum_asset_observations,
                specific_variance_prior_observations=float(
                    self._config.database_risk_specific_variance_prior_observations
                ),
                allow_ipo_specific_risk_prior=bool(
                    self._config.database_risk_allow_ipo_specific_prior
                ),
                date_index_kind="return_end_date",
            )
        except (ValueError, TypeError) as exc:
            raise CSI500DataContractError(
                f"database_risk_builder_blocked:{exc}"
            ) from exc
        root = pd.DataFrame(
            built["risk_root"],
            columns=list(map(str, built["risk_asset_codes"])),
        )
        unclassified_missing = int(
            (raw_prices.isna() & ~raw_suspend.astype(bool)).to_numpy().sum()
        )
        diagnostics = {
            **self.prefetch_audit,
            **built["diagnostics"],
            "provider": "database_point_in_time_qfq_factor_risk",
            "cache_hit": False,
            "signal_date": signal,
            "current_asset_count": len(codes),
            "requested_trading_day_lookback": int(
                self._config.database_risk_lookback_trading_days
            ),
            "price_dates": len(calendar),
            "prefetch_return_observations": int(len(all_returns)),
            "return_observations": int(len(returns)),
            "risk_window_policy": (
                "latest_configured_trade_calendar_returns;"
                "no_latest_ipo_common_window_truncation"
            ),
            "risk_window_start": str(returns.index.min()),
            "risk_window_end": str(returns.index.max()),
            "risk_window_observations": int(len(returns)),
            "minimum_factor_return_observations": minimum_factor_observations,
            "maximum_factor_return_observations": maximum_window,
            "minimum_asset_return_observations": minimum_asset_observations,
            "ipo_specific_risk_prior_enabled": bool(
                self._config.database_risk_allow_ipo_specific_prior
            ),
            "ipo_prior_asset_count": int(len(below_asset_floor)),
            "minimum_return_observations_per_asset": int(return_counts.min()),
            "maximum_return_observations_per_asset": int(return_counts.max()),
            "latest_first_valid_return_date": latest_first_valid,
            "latest_entry_assets": latest_entry_assets,
            "first_valid_return_date_hash": _hash_parts(first_valid_dates),
            "minimum_row_coverage_observed": float(row_coverage.min()),
            "minimum_asset_coverage_observed": float(asset_coverage.min()),
            "risk_window_missing_return_cells": int(
                returns.isna().to_numpy().sum()
            ),
            "individual_return_imputation_used": False,
            "sample_length_ratio_to_factor_maximum": sample_length_ratio,
            "sample_length_shrinkage_formula": (
                "base+(maximum-base)*(1-factor_observations/factor_maximum)"
            ),
            "base_diagonal_shrinkage": base_shrinkage,
            "maximum_diagonal_shrinkage": maximum_shrinkage,
            "sample_length_shrinkage_addon": (
                effective_shrinkage - base_shrinkage
            ),
            "effective_diagonal_shrinkage": effective_shrinkage,
            "short_sample_risk_policy": (
                "factor_window_shrinkage_plus_conservative_short_history_specific_prior"
            ),
            "latest_return_end_date": str(returns.index.max()),
            "strictly_before_signal": str(returns.index.max()) < signal,
            "future_data_used": False,
            "qfq_close_used": True,
            "zero_fill_used": False,
            "backfill_used": False,
            "unrestricted_forward_fill_used": False,
            "suspension_price_policy": (
                "forward_fill_only_explicit_suspend_timing_rows;"
                "reset_after_unclassified_gap"
            ),
            "explicit_suspension_rows": int(
                raw_suspend.to_numpy(dtype=bool).sum()
            ),
            "explicit_suspension_prices_filled": suspension_fill_count,
            "unclassified_missing_price_cells": unclassified_missing,
            "invalid_nonpositive_or_nonfinite_price_cells": int(
                invalid_price_mask.to_numpy().sum()
            ),
            "risk_asset_codes": list(map(str, built["risk_asset_codes"])),
        }
        result = {
            "risk_root": root,
            "annual_covariance": None,
            "risk_asset_codes": list(map(str, built["risk_asset_codes"])),
            "diagnostics": diagnostics,
        }
        self._cache[cache_key] = result
        return {
            **result,
            "risk_root": root.copy(),
            "diagnostics": dict(diagnostics),
        }


def build_database_point_in_time_risk_provider(
    connection: sqlite3.Connection,
    panel: pd.DataFrame,
    *,
    config: CSI500StrategyConfig,
) -> DatabasePointInTimeRiskProvider:
    """Create one prefetched daily risk provider for a database strategy run."""

    required = {"signal_date", "ts_code"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise CSI500DataContractError(
            "database_risk_panel_missing_columns:" + ",".join(missing)
        )
    return DatabasePointInTimeRiskProvider(
        connection,
        asset_codes=panel["ts_code"].astype(str).tolist(),
        signal_dates=panel["signal_date"].map(_date_text).tolist(),
        config=config,
    )

OptimizerCallable = Callable[..., dict[str, Any]]


def _date_text(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) < 8:
        raise CSI500DataContractError(f"invalid_date:{value}")
    return digits[:8]


def _optional_date_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    if not str(value).strip():
        return ""
    return _date_text(value)


def _trade_calendar_source_profile(
    connection: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    context: str = "trade_calendar",
) -> dict[str, Any]:
    """Return auditable calendar sources, verifying legacy blank sources by price rows.

    Older warehouse builds can contain a small number of trade-calendar rows whose
    ``source`` column is blank although the same date is present in the formal
    stock daily price table.  We do not mutate the database and we do not create a
    synthetic trading calendar.  Blank open-day rows are accepted only when at
    least one positive qfq close exists on the same trade date; otherwise the
    contract still blocks.
    """

    start_text = _date_text(start) if start else None
    end_text = _date_text(end) if end else None
    clauses: list[str] = []
    params: list[Any] = []
    if start_text:
        clauses.append("tc.trade_date >= ?")
        params.append(start_text)
    if end_text:
        clauses.append("tc.trade_date < ?")
        params.append(end_text)
    range_clause = ""
    if clauses:
        range_clause = " and " + " and ".join(clauses)

    source_rows = connection.execute(
        f"""
        select distinct trim(cast(tc.source as text))
        from trade_calendar tc
        where tc.source is not null
          and trim(cast(tc.source as text)) <> ''
          {range_clause}
        order by 1
        """,
        tuple(params),
    ).fetchall()
    sources = sorted({
        str(row[0]).strip() for row in source_rows
        if row[0] is not None and str(row[0]).strip()
    })

    count_row = connection.execute(
        f"""
        select
          count(*) as total_rows,
          sum(case when tc.source is null or trim(cast(tc.source as text)) = '' then 1 else 0 end) as missing_rows,
          sum(case when (tc.source is null or trim(cast(tc.source as text)) = '') and cast(tc.is_trade_day as integer)=1 then 1 else 0 end) as missing_trade_day_rows,
          sum(case when (tc.source is null or trim(cast(tc.source as text)) = '') and cast(tc.is_trade_day as integer)=0 then 1 else 0 end) as missing_nontrade_day_rows
        from trade_calendar tc
        where 1=1
          {range_clause}
        """,
        tuple(params),
    ).fetchone()
    total_rows = int(count_row[0] or 0) if count_row else 0
    missing_rows = int(count_row[1] or 0) if count_row else 0
    missing_trade_day_rows = int(count_row[2] or 0) if count_row else 0
    missing_nontrade_day_rows = int(count_row[3] or 0) if count_row else 0

    unverified_trade_day_rows = 0
    if missing_trade_day_rows > 0:
        price_table_exists = bool(connection.execute(
            """
            select 1
            from sqlite_master
            where type='table' and name='stock_ohlcv_daily'
            """
        ).fetchone())
        if not price_table_exists:
            unverified_trade_day_rows = missing_trade_day_rows
        else:
            unverified_trade_day_rows = int(connection.execute(
                f"""
                select count(*)
                from trade_calendar tc
                where (tc.source is null or trim(cast(tc.source as text)) = '')
                  and cast(tc.is_trade_day as integer)=1
                  {range_clause}
                  and not exists (
                    select 1
                    from stock_ohlcv_daily px
                    where px.trade_date = tc.trade_date
                      and px.qfq_close is not null
                      and cast(px.qfq_close as real) > 0
                  )
                """,
                tuple(params),
            ).fetchone()[0] or 0)
    verified_trade_day_rows = missing_trade_day_rows - unverified_trade_day_rows
    if unverified_trade_day_rows > 0:
        raise CSI500DataContractError(
            f"{context}_source_missing_unverified_by_stock_prices:"
            f"{unverified_trade_day_rows}"
        )
    if verified_trade_day_rows > 0:
        sources.append("stock_ohlcv_daily_calendar_verified_missing_source")
    if not sources:
        raise CSI500DataContractError(f"{context}_source_missing")

    return {
        "sources": sources,
        "audit": {
            "calendar_source_total_rows": total_rows,
            "calendar_source_missing_rows": missing_rows,
            "calendar_source_missing_trade_day_rows": missing_trade_day_rows,
            "calendar_source_missing_nontrade_day_rows": missing_nontrade_day_rows,
            "calendar_source_missing_trade_day_rows_verified_by_price": verified_trade_day_rows,
            "calendar_source_missing_trade_day_rows_unverified_by_price": unverified_trade_day_rows,
            "calendar_source_verification_rule": "blank_source_open_days_require_positive_stock_ohlcv_qfq_close_same_date",
            "calendar_source_start": start_text or "",
            "calendar_source_end_exclusive": end_text or "",
        },
    }


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CSI500DataContractError(f"{name}_must_be_numeric") from exc
    if not math.isfinite(number):
        raise CSI500DataContractError(f"{name}_must_be_finite")
    return number


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple, set)):
        return [_canonical(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        return round(value, 14)
    return value


def _hash_parts(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, pd.DataFrame):
            frame = part.copy()
            frame.columns = [str(column) for column in frame.columns]
            digest.update(json.dumps(frame.columns.tolist()).encode("utf-8"))
            digest.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
        elif isinstance(part, np.ndarray):
            array = np.ascontiguousarray(part)
            digest.update(str(array.shape).encode("ascii"))
            digest.update(array.tobytes())
        else:
            digest.update(
                json.dumps(
                    _canonical(part), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
    return digest.hexdigest()


def _validate_config(config: CSI500StrategyConfig) -> None:
    integer_fields = {
        "expected_members": config.expected_members,
        "minimum_members": config.minimum_members,
        "factor_lookback": config.factor_lookback,
        "factor_min_history": config.factor_min_history,
        "minimum_ic_cross_section": config.minimum_ic_cross_section,
        "missing_member_max_quote_staleness_trading_days": (
            config.missing_member_max_quote_staleness_trading_days
        ),
        "benchmark_mtm_max_exit_staleness_trading_days": (
            config.benchmark_mtm_max_exit_staleness_trading_days
        ),
        "missing_member_max_factor_staleness_periods": (
            config.missing_member_max_factor_staleness_periods
        ),
        "risk_lookback": config.risk_lookback,
        "risk_min_observations": config.risk_min_observations,
        "database_risk_lookback_trading_days": config.database_risk_lookback_trading_days,
        "database_risk_min_return_observations": config.database_risk_min_return_observations,
        "database_risk_min_asset_return_observations": (
            config.database_risk_min_asset_return_observations
        ),
        "database_risk_max_common_return_observations": (
            config.database_risk_max_common_return_observations
        ),
        "trading_days_per_period": config.trading_days_per_period,
        "periods_per_year": config.periods_per_year,
    }
    for name, value in integer_fields.items():
        if isinstance(value, bool) or int(value) != value or int(value) <= 0:
            raise CSI500DataContractError(f"{name}_must_be_positive_integer")
    if config.minimum_members > config.expected_members:
        raise CSI500DataContractError("minimum_members_exceeds_expected_members")
    if config.optimizer.target_holdings != 50:
        raise CSI500DataContractError("optimizer_target_holdings_must_equal_50")
    if config.factor_min_history > config.factor_lookback:
        raise CSI500DataContractError("factor_min_history_exceeds_lookback")
    mean_prior_std = _finite(
        config.factor_ic_mean_prior_std, "factor_ic_mean_prior_std"
    )
    if mean_prior_std <= 0.0:
        raise CSI500DataContractError("factor_ic_mean_prior_std_must_be_positive")
    covariance_shrinkage = _finite(
        config.factor_ic_covariance_shrinkage,
        "factor_ic_covariance_shrinkage",
    )
    if not 0.0 <= covariance_shrinkage <= 1.0:
        raise CSI500DataContractError(
            "factor_ic_covariance_shrinkage_must_be_in_0_1"
        )
    if _finite(
        config.factor_ic_covariance_ridge, "factor_ic_covariance_ridge"
    ) <= 0.0:
        raise CSI500DataContractError(
            "factor_ic_covariance_ridge_must_be_positive"
        )
    absolute_weight_cap = _finite(
        config.factor_absolute_weight_cap, "factor_absolute_weight_cap"
    )
    if not 0.0 < absolute_weight_cap <= 1.0:
        raise CSI500DataContractError(
            "factor_absolute_weight_cap_must_be_in_0_1"
        )
    if config.factor_weight_method not in {
        "adaptive_bayesian_icir", "precision", "walkforward_positive_ic"
    }:
        raise CSI500DataContractError(
            "factor_weight_method_must_be_adaptive_bayesian_icir_"
            "precision_or_walkforward_positive_ic"
        )
    hit_rate = _finite(
        config.walkforward_positive_ic_min_hit_rate,
        "walkforward_positive_ic_min_hit_rate",
    )
    if not 0.0 <= hit_rate <= 1.0:
        raise CSI500DataContractError(
            "walkforward_positive_ic_min_hit_rate_must_be_in_0_1"
        )
    if _finite(
        config.factor_evidence_prior_observations,
        "factor_evidence_prior_observations",
    ) <= 0.0:
        raise CSI500DataContractError(
            "factor_evidence_prior_observations_must_be_positive"
        )
    if _finite(
        config.factor_evidence_clip, "factor_evidence_clip"
    ) <= 0.0:
        raise CSI500DataContractError(
            "factor_evidence_clip_must_be_positive"
        )
    if str(config.score_source_mode) not in {
        "causal_recompute", "precomputed_database", "factor_lab_champion"
    }:
        raise CSI500DataContractError(
            "score_source_mode_must_be_causal_recompute_precomputed_database_or_factor_lab_champion"
        )
    if str(config.factor_lab_profile) not in {
        "strict_turnover_065", "high_sharpe_enhanced"
    }:
        raise CSI500DataContractError(
            "factor_lab_profile_must_be_strict_turnover_065_or_high_sharpe_enhanced"
        )
    score_coverage = _finite(
        config.precomputed_score_min_coverage,
        "precomputed_score_min_coverage",
    )
    if not 0.0 < score_coverage <= 1.0:
        raise CSI500DataContractError(
            "precomputed_score_min_coverage_must_be_in_0_1"
        )
    for name in (
        "factor_lab_bridge_screen_min_coverage",
    ):
        value = _finite(getattr(config, name), name)
        if not 0.0 < value <= 1.0:
            raise CSI500DataContractError(f"{name}_must_be_in_0_1")
    for name in (
        "factor_lab_bridge_max_factor_candidates",
        "factor_lab_bridge_screen_top_n",
        "factor_lab_bridge_screen_lookback_days",
        "factor_lab_bridge_screen_rebalance_days",
        "factor_lab_bridge_screen_min_dates",
        "factor_lab_bridge_screen_min_assets_per_date",
        "factor_lab_bridge_lookback_periods",
        "factor_lab_bridge_min_periods",
        "factor_lab_bridge_min_training_periods",
        "factor_lab_bridge_min_training_rows",
    ):
        value = getattr(config, name)
        if isinstance(value, bool) or int(value) != value or int(value) <= 0:
            raise CSI500DataContractError(f"{name}_must_be_positive_integer")
    max_pair_corr = _finite(
        config.factor_lab_bridge_screen_max_pair_corr,
        "factor_lab_bridge_screen_max_pair_corr",
    )
    if not 0.0 < max_pair_corr < 1.0:
        raise CSI500DataContractError(
            "factor_lab_bridge_screen_max_pair_corr_must_be_in_0_1_open"
        )
    staleness = config.factor_lab_bridge_external_factor_max_staleness_days
    if isinstance(staleness, bool) or int(staleness) != staleness or int(staleness) < 0:
        raise CSI500DataContractError(
            "factor_lab_bridge_external_factor_max_staleness_days_must_be_nonnegative_integer"
        )

    if config.missing_member_max_factor_staleness_periods != 1:
        raise CSI500DataContractError(
            "missing_member_max_factor_staleness_periods_must_equal_one"
        )
    for name in (
        "factor_min_coverage", "style_min_coverage", "risk_min_asset_coverage",
        "database_risk_min_row_coverage",
    ):
        value = _finite(getattr(config, name), name)
        if not 0.0 < value <= 1.0:
            raise CSI500DataContractError(f"{name}_must_be_in_0_1")
    for name in (
        "raw_index_weight_total", "raw_index_weight_tolerance",
        "normalized_weight_tolerance", "neutralization_ridge",
        "neutralization_tolerance", "portfolio_notional",
        "max_adv_participation", "default_trade_limit",
        "liquidity_amount_to_cny_multiplier", "transaction_cost_rate",
    ):
        if _finite(getattr(config, name), name) < 0.0:
            raise CSI500DataContractError(f"{name}_must_be_nonnegative")
    if config.portfolio_notional <= 0.0:
        raise CSI500DataContractError("portfolio_notional_must_be_positive")
    if config.raw_index_weight_total <= 0.0:
        raise CSI500DataContractError("raw_index_weight_total_must_be_positive")
    if config.database_risk_min_return_observations < 20:
        raise CSI500DataContractError(
            "database_risk_min_return_observations_must_be_at_least_20"
        )
    if config.database_risk_min_asset_return_observations < 20:
        raise CSI500DataContractError(
            "database_risk_min_asset_return_observations_must_be_at_least_20"
        )
    if (
        config.database_risk_min_asset_return_observations
        > config.database_risk_max_common_return_observations
    ):
        raise CSI500DataContractError(
            "database_risk_asset_minimum_exceeds_factor_maximum"
        )
    if _finite(
        config.database_risk_specific_variance_prior_observations,
        "database_risk_specific_variance_prior_observations",
    ) <= 0.0:
        raise CSI500DataContractError(
            "database_risk_specific_variance_prior_observations_must_be_positive"
        )
    amount_unit = str(config.liquidity_amount_unit).strip().lower()
    if amount_unit not in AMOUNT_UNIT_TO_CNY_MULTIPLIER:
        raise CSI500DataContractError(
            "unsupported_liquidity_amount_unit:"
            + str(config.liquidity_amount_unit)
        )
    expected_multiplier = AMOUNT_UNIT_TO_CNY_MULTIPLIER[amount_unit]
    actual_multiplier = _finite(
        config.liquidity_amount_to_cny_multiplier,
        "liquidity_amount_to_cny_multiplier",
    )
    if actual_multiplier <= 0.0:
        raise CSI500DataContractError(
            "liquidity_amount_to_cny_multiplier_must_be_positive"
        )
    if not math.isclose(
        actual_multiplier, expected_multiplier, rel_tol=0.0, abs_tol=1.0e-12
    ):
        raise CSI500DataContractError(
            "liquidity_amount_unit_multiplier_mismatch:"
            f"{amount_unit}:expected={expected_multiplier}:actual={actual_multiplier}"
        )
    if not 0.0 < float(config.max_adv_participation) <= 1.0:
        raise CSI500DataContractError(
            "max_adv_participation_must_be_in_0_1"
        )
    if (
        config.database_risk_min_return_observations
        > config.database_risk_max_common_return_observations
    ):
        raise CSI500DataContractError(
            "database_risk_common_minimum_exceeds_common_maximum"
        )
    if (
        config.database_risk_max_common_return_observations
        > config.database_risk_lookback_trading_days
    ):
        raise CSI500DataContractError(
            "database_risk_common_maximum_exceeds_prefetch_lookback"
        )
    if _finite(config.database_risk_half_life, "database_risk_half_life") <= 0.0:
        raise CSI500DataContractError("database_risk_half_life_must_be_positive")
    base_shrinkage = _finite(
        config.database_risk_base_diagonal_shrinkage,
        "database_risk_base_diagonal_shrinkage",
    )
    maximum_shrinkage = _finite(
        config.database_risk_max_diagonal_shrinkage,
        "database_risk_max_diagonal_shrinkage",
    )
    if not 0.0 <= base_shrinkage <= maximum_shrinkage <= 1.0:
        raise CSI500DataContractError(
            "database_risk_shrinkage_must_satisfy_0_le_base_le_max_le_1"
        )
    max_daily_return = _finite(
        config.database_risk_max_abs_daily_return,
        "database_risk_max_abs_daily_return",
    )
    if not 0.0 < max_daily_return <= 1.0:
        raise CSI500DataContractError(
            "database_risk_max_abs_daily_return_must_be_in_0_1"
        )

    if config.raw_index_weight_tolerance >= config.raw_index_weight_total:
        raise CSI500DataContractError("raw_index_weight_tolerance_must_be_below_total")
    if not config.factor_columns:
        raise CSI500DataContractError("factor_columns_must_not_be_empty")
    if len(set(config.factor_columns)) != len(config.factor_columns):
        raise CSI500DataContractError("duplicate_factor_columns")
    if len(set(config.style_columns)) != len(config.style_columns):
        raise CSI500DataContractError("duplicate_style_columns")
    optimizer_styles = tuple(
        str(column) for column in config.optimizer_style_columns
        if str(column) in set(map(str, config.style_columns))
    )
    if config.style_columns and not optimizer_styles:
        raise CSI500DataContractError(
            "optimizer_style_columns_must_overlap_configured_style_columns"
        )
    if _date_text(config.train_end) >= _date_text(config.validation_end):
        raise CSI500DataContractError("train_end_must_precede_validation_end")
    if config.formal_evaluation_start is not None:
        formal_start = _date_text(config.formal_evaluation_start)
        if formal_start > _date_text(config.validation_end):
            raise CSI500DataContractError(
                "formal_evaluation_start_must_not_exceed_validation_end"
            )
    if config.test_role != "report_only":
        raise CSI500DataContractError("test_role_must_be_report_only")


def _effective_optimizer_style_columns(config: CSI500StrategyConfig) -> tuple[str, ...]:
    """Resolve optimizer style columns from the explicit style contract.

    Synthetic and ablation runs may intentionally provide only a subset such as
    size/value.  Production keeps the default four-factor optimizer exposure set
    because all four columns are also present in ``style_columns``.
    """

    configured = set(map(str, config.style_columns))
    return tuple(
        str(column) for column in config.optimizer_style_columns
        if str(column) in configured
    )


def load_csi500_constituents(
    connection: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    config: CSI500StrategyConfig | None = None,
) -> pd.DataFrame:
    """Load exact signal-date CSI500 weights and normalize percent to one."""

    config = config or CSI500StrategyConfig()
    _validate_config(config)
    columns = {
        row[1] for row in connection.execute(
            "pragma table_info(index_constituent_period)"
        ).fetchall()
    }
    required = {
        "universe", "index_code", "trade_date", "con_code", "weight", "status",
    }
    if not required.issubset(columns):
        missing = sorted(required - columns)
        raise CSI500DataContractError(
            "index_constituent_period_missing_columns:" + ",".join(missing)
        )
    clauses = ["universe=?", "index_code=?", "status='ready'"]
    params: list[Any] = [config.universe, config.index_code]
    if start is not None:
        clauses.append("trade_date>=?")
        params.append(_date_text(start))
    if end is not None:
        clauses.append("trade_date<=?")
        params.append(_date_text(end))
    source_expression = "coalesce(source, '')" if "source" in columns else "''"
    rows = connection.execute(
        "select trade_date, con_code, weight, index_code, "
        + source_expression
        + " from index_constituent_period where "
        + " and ".join(clauses)
        + " order by trade_date, con_code",
        params,
    ).fetchall()
    if not rows:
        raise CSI500DataContractError("no_exact_csi500_constituents")
    frame = pd.DataFrame(
        rows,
        columns=["signal_date", "ts_code", "raw_benchmark_weight", "index_code", "source"],
    )
    frame["signal_date"] = frame["signal_date"].map(_date_text)
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip()
    frame["raw_benchmark_weight"] = pd.to_numeric(
        frame["raw_benchmark_weight"], errors="coerce"
    )
    accepted: list[pd.DataFrame] = []
    for signal_date, group in frame.groupby("signal_date", sort=True):
        group = group.copy().sort_values("ts_code", kind="mergesort")
        count = len(group)
        if group["ts_code"].duplicated().any() or (group["ts_code"] == "").any():
            raise CSI500DataContractError(f"duplicate_or_empty_member:{signal_date}")
        if count < config.minimum_members:
            raise CSI500DataContractError(
                f"csi500_member_count_below_{config.minimum_members}:{signal_date}:{count}"
            )
        if config.require_exact_members and count != config.expected_members:
            raise CSI500DataContractError(
                f"csi500_member_count_not_{config.expected_members}:{signal_date}:{count}"
            )
        weights = group["raw_benchmark_weight"].to_numpy(dtype=float)
        if not np.isfinite(weights).all() or np.any(weights <= 0.0):
            raise CSI500DataContractError(f"invalid_index_weights:{signal_date}")
        total = float(weights.sum())
        if abs(total - config.raw_index_weight_total) > config.raw_index_weight_tolerance:
            raise CSI500DataContractError(
                f"index_weight_total_not_approximately_100:{signal_date}:{total:.10f}"
            )
        group["benchmark_weight"] = weights / total
        group["raw_benchmark_weight_total"] = total
        group["benchmark_weight_unit"] = "percent"
        group["benchmark_weight_normalization_json"] = json.dumps({
            "signal_date": signal_date,
            "status": "normalized",
            "member_count": count,
            "expected_members": config.expected_members,
            "input_unit": "percent",
            "input_total": total,
            "accepted_total_range": [
                config.raw_index_weight_total - config.raw_index_weight_tolerance,
                config.raw_index_weight_total + config.raw_index_weight_tolerance,
            ],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        accepted.append(group)
    return pd.concat(accepted, ignore_index=True)[
        [
            "signal_date", "ts_code", "benchmark_weight",
            "raw_benchmark_weight", "raw_benchmark_weight_total",
            "benchmark_weight_unit", "benchmark_weight_normalization_json",
            "index_code", "source",
        ]
    ]


def optimizer_factor_score_table_ddl() -> str:
    return f"""
    create table if not exists {SCORE_TABLE} (
      score_run_id text not null,
      signal_date text not null,
      maturity_date text not null,
      ts_code text not null,
      score_name text not null,
      score real not null,
      raw_score real not null,
      benchmark_weight real not null,
      industry text not null,
      style_size real,
      style_value real,
      style_momentum real,
      style_liquidity real,
      style_json text not null,
      factor_weights_json text not null,
      neutralization_json text not null,
      source_hash text not null,
      primary key (score_run_id, signal_date, ts_code, score_name)
    )
    """


def create_optimizer_factor_score_table(connection: sqlite3.Connection) -> None:
    connection.execute(optimizer_factor_score_table_ddl())


def persist_optimizer_factor_scores(
    connection: sqlite3.Connection,
    score_frame: pd.DataFrame,
    *,
    score_run_id: str,
    score_name: str | None = None,
) -> int:
    """Atomically persist deterministic scores; conflicting evidence blocks."""

    if not score_run_id or not str(score_run_id).strip():
        raise CSI500DataContractError("score_run_id_required")
    if score_frame.empty:
        return 0
    score_name = score_name or str(score_frame["score_name"].iloc[0])
    required = {
        "signal_date", "maturity_date", "ts_code", "score", "raw_score",
        "benchmark_weight", "industry", "source_hash", "factor_weights_json",
        "neutralization_json",
    }
    missing = sorted(required - set(score_frame.columns))
    if missing:
        raise CSI500DataContractError(
            "score_frame_missing_columns:" + ",".join(missing)
        )
    rows: list[tuple[Any, ...]] = []
    ordered = score_frame.sort_values(["signal_date", "ts_code"], kind="mergesort")
    for row in ordered.to_dict("records"):
        styles = {
            key: None if pd.isna(row.get(key)) else float(row[key])
            for key in (
                "style_size", "style_value", "style_momentum", "style_liquidity"
            )
            if key in row
        }
        rows.append((
            str(score_run_id), _date_text(row["signal_date"]),
            _date_text(row["maturity_date"]), str(row["ts_code"]), str(score_name),
            _finite(row["score"], "score"), _finite(row["raw_score"], "raw_score"),
            _finite(row["benchmark_weight"], "benchmark_weight"),
            str(row["industry"]), styles.get("style_size"), styles.get("style_value"),
            styles.get("style_momentum"), styles.get("style_liquidity"),
            json.dumps(styles, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            str(row["factor_weights_json"]), str(row["neutralization_json"]),
            str(row["source_hash"]),
        ))
    with connection:
        create_optimizer_factor_score_table(connection)
        for record in rows:
            existing = connection.execute(
                f"select source_hash from {SCORE_TABLE} "
                "where score_run_id=? and signal_date=? and ts_code=? and score_name=?",
                (record[0], record[1], record[3], record[4]),
            ).fetchone()
            if existing is not None and str(existing[0]) != record[-1]:
                raise CSI500DataContractError(
                    "score_primary_key_conflicts_with_different_source_hash:"
                    f"{record[0]}:{record[1]}:{record[3]}:{record[4]}"
                )
        connection.executemany(
            f"""
            insert or ignore into {SCORE_TABLE}
            (score_run_id, signal_date, maturity_date, ts_code, score_name,
             score, raw_score, benchmark_weight, industry,
             style_size, style_value, style_momentum, style_liquidity,
             style_json, factor_weights_json, neutralization_json, source_hash)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def _validate_missing_member_pit_tables(
    connection: sqlite3.Connection,
) -> None:
    required = {
        "stock_ohlcv_daily": {
            "trade_date", "ts_code", "open", "high", "low", "close",
            "qfq_close", "up_limit", "down_limit", "suspend_timing",
        },
        "trade_calendar": {"trade_date", "is_trade_day"},
    }
    for table, expected in required.items():
        observed = {
            str(row[1]) for row in connection.execute(
                f"pragma table_info({table})"
            ).fetchall()
        }
        missing = sorted(expected - observed)
        if missing:
            raise CSI500DataContractError(
                f"{table}_missing_pit_completion_columns:" + ",".join(missing)
            )


def _latest_pre_signal_quote_evidence(
    connection: sqlite3.Connection,
    *,
    ts_code: str,
    signal_date: str,
    max_staleness_trading_days: int,
) -> dict[str, Any]:
    row = connection.execute(
        """
        select trade_date, qfq_close, suspend_timing
        from stock_ohlcv_daily
        where ts_code=? and trade_date<=? and qfq_close>0
        order by trade_date desc
        limit 1
        """,
        (str(ts_code), _date_text(signal_date)),
    ).fetchone()
    audit: dict[str, Any] = {
        "ts_code": str(ts_code),
        "signal_date": _date_text(signal_date),
        "policy": "latest_positive_qfq_close_on_or_before_signal",
        "maximum_staleness_trading_days": int(max_staleness_trading_days),
        "future_quote_used": False,
        "backfill_used": False,
    }
    if row is None:
        return {
            **audit,
            "status": "blocked",
            "reason": "missing_pre_signal_quote_history",
            "quote_date": None,
            "staleness_trading_days": None,
        }
    quote_date = _date_text(row[0])
    if quote_date > _date_text(signal_date):
        raise CSI500DataContractError(
            f"pit_quote_after_signal_detected:{signal_date}:{ts_code}:{quote_date}"
        )
    staleness = int(connection.execute(
        """
        select count(*)
        from trade_calendar
        where is_trade_day=1 and trade_date>? and trade_date<=?
        """,
        (quote_date, _date_text(signal_date)),
    ).fetchone()[0])
    audit.update({
        "quote_date": quote_date,
        "quote_qfq_close": _finite(row[1], "pit_quote_qfq_close"),
        "quote_suspend_timing": "" if row[2] is None else str(row[2]),
        "staleness_trading_days": staleness,
    })
    if staleness > int(max_staleness_trading_days):
        audit.update({
            "status": "blocked",
            "reason": (
                "pre_signal_quote_staleness_exceeds_limit:"
                f"{staleness}>{int(max_staleness_trading_days)}"
            ),
        })
        return audit
    audit["status"] = "ready"
    return audit


def _post_signal_label_evidence(
    connection: sqlite3.Connection,
    *,
    ts_code: str,
    signal_date: str,
    maturity_date: str,
) -> dict[str, Any]:
    signal = _date_text(signal_date)
    maturity = _date_text(maturity_date)
    audit: dict[str, Any] = {
        "ts_code": str(ts_code),
        "signal_date": signal,
        "maturity_date": maturity,
        "policy": (
            "first_actual_tradable_entry_strictly_after_signal_to_"
            "last_actual_positive_qfq_close_on_or_before_maturity"
        ),
        "entry_lower_bound": signal,
        "entry_lower_bound_inclusive": False,
        "maturity_upper_bound": maturity,
        "maturity_upper_bound_inclusive": True,
        "backfill_used": False,
        "pre_or_on_signal_price_used": False,
        "future_beyond_maturity_used": False,
    }
    entry = connection.execute(
        """
        select trade_date, qfq_close * open / nullif(close, 0)
        from stock_ohlcv_daily
        where ts_code=?
          and trade_date>?
          and trade_date<=?
          and qfq_close>0
          and open>0
          and close>0
          and suspend_timing is null
          and not (
            up_limit is not null
            and open>=up_limit*0.995
            and low>=up_limit*0.995
          )
          and not (
            down_limit is not null
            and open<=down_limit*1.005
            and high<=down_limit*1.005
          )
        order by trade_date
        limit 1
        """,
        (str(ts_code), signal, maturity),
    ).fetchone()
    exit_row = connection.execute(
        """
        select trade_date, qfq_close
        from stock_ohlcv_daily
        where ts_code=?
          and trade_date>?
          and trade_date<=?
          and qfq_close>0
        order by trade_date desc
        limit 1
        """,
        (str(ts_code), signal, maturity),
    ).fetchone()
    if entry is None:
        return {
            **audit,
            "status": "blocked",
            "reason": "missing_actual_post_signal_tradable_entry",
            "entry_trade_date": None,
            "exit_trade_date": None if exit_row is None else _date_text(exit_row[0]),
        }
    if exit_row is None:
        return {
            **audit,
            "status": "blocked",
            "reason": "missing_actual_post_signal_exit_by_maturity",
            "entry_trade_date": _date_text(entry[0]),
            "exit_trade_date": None,
        }
    entry_date = _date_text(entry[0])
    exit_date = _date_text(exit_row[0])
    entry_price = _finite(entry[1], "pit_label_entry_price")
    exit_price = _finite(exit_row[1], "pit_label_exit_price")
    if not (
        signal < entry_date <= exit_date <= maturity
        and entry_price > 0.0
        and exit_price > 0.0
    ):
        return {
            **audit,
            "status": "blocked",
            "reason": "invalid_actual_post_signal_label_bounds_or_prices",
            "entry_trade_date": entry_date,
            "exit_trade_date": exit_date,
        }
    audit.update({
        "status": "ready",
        "entry_trade_date": entry_date,
        "exit_trade_date": exit_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "label_next_ret": exit_price / entry_price - 1.0,
    })
    return audit


class DatabasePointInTimeHoldingValuationProvider:
    """PIT qfq mark-to-market reserved for already-held securities."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        config: CSI500StrategyConfig,
    ) -> None:
        _validate_config(config)
        price_columns = {
            str(row[1]) for row in connection.execute(
                "pragma table_info(stock_ohlcv_daily)"
            ).fetchall()
        }
        missing_price_columns = sorted(
            {"trade_date", "ts_code", "qfq_close"} - price_columns
        )
        if missing_price_columns:
            raise CSI500DataContractError(
                "holding_valuation_price_columns_missing:"
                + ",".join(missing_price_columns)
            )
        calendar_columns = {
            str(row[1]) for row in connection.execute(
                "pragma table_info(trade_calendar)"
            ).fetchall()
        }
        missing_calendar_columns = sorted(
            {"trade_date", "is_trade_day", "source"} - calendar_columns
        )
        if missing_calendar_columns:
            raise CSI500DataContractError(
                "holding_valuation_calendar_columns_missing:"
                + ",".join(missing_calendar_columns)
            )
        calendar_rows = connection.execute(
            """
            select trade_date
            from trade_calendar
            group by trade_date
            having max(cast(is_trade_day as integer))=1
            order by trade_date
            """
        ).fetchall()
        calendar_source_profile = _trade_calendar_source_profile(
            connection,
            context="holding_valuation_trade_calendar",
        )
        if not calendar_rows:
            raise CSI500DataContractError(
                "holding_valuation_trade_calendar_empty"
            )
        self.connection = connection
        self.config = config
        self.calendar_dates = np.asarray(
            [_date_text(row[0]) for row in calendar_rows], dtype=str
        )
        self.calendar_date_set = set(self.calendar_dates.tolist())
        self.calendar_sources = list(calendar_source_profile["sources"])
        self.prefetch_audit = {
            "provider": "database_point_in_time_existing_holding_valuation",
            "price_source": "stock_ohlcv_daily.qfq_close",
            "calendar_source": self.calendar_sources,
            "maximum_end_quote_staleness_trading_days": int(
                config.benchmark_mtm_max_exit_staleness_trading_days
            ),
            "usage_restricted_to_previous_holdings": True,
            "permitted_for_new_positions": False,
            "permitted_for_alpha_or_ic": False,
            "permitted_for_comparator_establishment": False,
            "future_quote_used": False,
        }
        self.prefetch_audit.update(calendar_source_profile["audit"])

    def _latest_quote(
        self,
        ts_code: str,
        upper_bound: str,
    ) -> tuple[str, float] | None:
        row = self.connection.execute(
            """
            select trade_date, qfq_close
            from stock_ohlcv_daily
            where ts_code=? and trade_date<=? and qfq_close>0
            order by trade_date desc
            limit 1
            """,
            (str(ts_code), _date_text(upper_bound)),
        ).fetchone()
        if row is None:
            return None
        return _date_text(row[0]), _finite(
            row[1], "holding_valuation_qfq_close"
        )

    def __call__(
        self,
        signal_date: str,
        maturity_date: str,
        codes: Sequence[str],
    ) -> Mapping[str, Any]:
        signal = _date_text(signal_date)
        maturity = _date_text(maturity_date)
        requested = sorted({str(code) for code in codes if str(code)})
        maximum_staleness = int(
            self.config.benchmark_mtm_max_exit_staleness_trading_days
        )
        assets: dict[str, dict[str, Any]] = {}
        returns: dict[str, float] = {}
        for code in requested:
            start_quote = self._latest_quote(code, signal)
            end_quote = self._latest_quote(code, maturity)
            reason: str | None = None
            start_date: str | None = None
            end_date: str | None = None
            start_price: float | None = None
            end_price: float | None = None
            staleness: int | None = None
            if signal not in self.calendar_date_set:
                reason = "holding_valuation_signal_not_authoritative_trade_day"
            elif maturity not in self.calendar_date_set:
                reason = "holding_valuation_maturity_not_authoritative_trade_day"
            elif start_quote is None:
                reason = "holding_valuation_missing_start_quote"
            elif end_quote is None:
                reason = "holding_valuation_missing_end_quote"
            else:
                start_date, start_price = start_quote
                end_date, end_price = end_quote
                if not (start_date <= signal and start_date <= end_date <= maturity):
                    reason = "holding_valuation_invalid_quote_date_bounds"
                else:
                    staleness = int(
                        np.searchsorted(
                            self.calendar_dates, maturity, side="right"
                        )
                        - np.searchsorted(
                            self.calendar_dates, end_date, side="right"
                        )
                    )
                    if staleness < 0:
                        reason = "holding_valuation_negative_end_staleness"
                    elif staleness > maximum_staleness:
                        reason = (
                            "holding_valuation_end_quote_staleness_exceeds_limit:"
                            f"{staleness}>{maximum_staleness}"
                        )
                    else:
                        returns[code] = end_price / start_price - 1.0
            assets[code] = {
                "status": "ready" if reason is None else "blocked",
                "reason": reason,
                "ts_code": code,
                "signal_date": signal,
                "maturity_date": maturity,
                "start_quote_date": start_date,
                "end_quote_date": end_date,
                "start_quote_price": start_price,
                "end_quote_price": end_price,
                "end_quote_staleness_trading_days": staleness,
                "maximum_end_quote_staleness_trading_days": maximum_staleness,
                "stale_price_carried_forward": bool(
                    end_date is not None and end_date < maturity
                ),
                "existing_holding_valuation_only": True,
                "permitted_for_new_positions": False,
                "permitted_for_alpha_or_ic": False,
                "permitted_for_comparator_establishment": False,
                "price_source": "stock_ohlcv_daily.qfq_close",
                "calendar_source": self.calendar_sources,
                "start_quote_policy": (
                    "last_positive_qfq_on_or_before_signal"
                ),
                "end_quote_policy": (
                    "last_positive_qfq_on_or_before_maturity"
                ),
                "future_quote_used": False,
            }
        blocked = {
            code: audit["reason"] for code, audit in assets.items()
            if audit["status"] != "ready"
        }
        return {
            "status": "ready" if not blocked else "blocked",
            "signal_date": signal,
            "maturity_date": maturity,
            "requested_codes": requested,
            "requested_code_count": len(requested),
            "returns": returns,
            "assets": assets,
            "blocked_reasons": blocked,
            "usage_restricted_to_previous_holdings": True,
            "permitted_for_new_positions": False,
            "permitted_for_alpha_or_ic": False,
            "permitted_for_comparator_establishment": False,
            "future_quote_used": False,
        }

def _attach_benchmark_mark_to_market_returns(
    connection: sqlite3.Connection,
    panel: pd.DataFrame,
    *,
    config: CSI500StrategyConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach benchmark-only close-to-close valuation returns.

    This return is never an executable asset label.  It carries the last
    positive qfq close forward only for benchmark valuation and blocks when
    the period-end quote is more than the configured number of authoritative
    trading days stale.
    """

    required = {"signal_date", "maturity_date", "ts_code"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise CSI500DataContractError(
            "benchmark_mtm_panel_missing_columns:" + ",".join(missing)
        )
    calendar_rows = connection.execute(
        """
        select trade_date
        from trade_calendar
        group by trade_date
        having max(cast(is_trade_day as integer))=1
        order by trade_date
        """
    ).fetchall()
    calendar_source_profile = _trade_calendar_source_profile(
        connection,
        context="benchmark_mtm_trade_calendar",
    )
    if not calendar_rows:
        raise CSI500DataContractError("benchmark_mtm_trade_calendar_empty")
    calendar_dates = np.asarray(
        [_date_text(row[0]) for row in calendar_rows], dtype=str
    )
    calendar_sources = list(calendar_source_profile["sources"])

    frame = panel.copy()
    records: list[dict[str, Any]] = []
    returns: list[float] = []
    statuses: list[str] = []
    reasons: list[str | None] = []
    start_dates: list[str] = []
    end_dates: list[str] = []
    staleness_values: list[int | None] = []
    carried_flags: list[bool] = []
    audit_json: list[str] = []
    maximum_staleness = int(
        config.benchmark_mtm_max_exit_staleness_trading_days
    )

    for row in frame.to_dict("records"):
        signal_date = _date_text(row["signal_date"])
        maturity_date = _date_text(row["maturity_date"])
        start_date = (
            _optional_date_text(
                row.get("pit_latest_pre_signal_quote_date")
            )
            or signal_date
        )
        preferred_start_price = pd.to_numeric(
            row.get("pit_latest_pre_signal_quote_price"), errors="coerce"
        )
        fallback_start_price = pd.to_numeric(
            row.get("px"), errors="coerce"
        )
        start_price = (
            float(preferred_start_price)
            if pd.notna(preferred_start_price)
            and math.isfinite(float(preferred_start_price))
            and float(preferred_start_price) > 0.0
            else float(fallback_start_price)
            if pd.notna(fallback_start_price)
            and math.isfinite(float(fallback_start_price))
            and float(fallback_start_price) > 0.0
            else math.nan
        )
        observed_exit_date = _optional_date_text(row.get("exit_trade_date"))
        observed_exit_price = pd.to_numeric(
            row.get("px_next"), errors="coerce"
        )
        has_observed_exit = bool(
            observed_exit_date
            and pd.notna(observed_exit_price)
            and math.isfinite(float(observed_exit_price))
            and float(observed_exit_price) > 0.0
        )
        if has_observed_exit:
            end_date = observed_exit_date
            end_price = float(observed_exit_price)
        else:
            end_date = start_date
            end_price = start_price
        carried_forward = bool(end_date < maturity_date)

        reason: str | None = None
        staleness: int | None = None
        mark_to_market_return = math.nan
        if not math.isfinite(start_price) or start_price <= 0.0:
            reason = "benchmark_mtm_missing_positive_start_quote"
        elif not math.isfinite(end_price) or end_price <= 0.0:
            reason = "benchmark_mtm_missing_positive_end_quote"
        elif not (
            start_date <= signal_date
            and start_date <= end_date <= maturity_date
        ):
            reason = "benchmark_mtm_invalid_quote_date_bounds"
        else:
            maturity_position = int(np.searchsorted(
                calendar_dates, maturity_date, side="right"
            ))
            quote_position = int(np.searchsorted(
                calendar_dates, end_date, side="right"
            ))
            staleness = maturity_position - quote_position
            if staleness < 0:
                reason = "benchmark_mtm_negative_quote_staleness"
            elif staleness > maximum_staleness:
                reason = (
                    "benchmark_mtm_exit_quote_staleness_exceeds_limit:"
                    f"{staleness}>{maximum_staleness}"
                )
            else:
                mark_to_market_return = end_price / start_price - 1.0

        status = "ready" if reason is None else "blocked"
        audit = {
            "status": status,
            "reason": reason,
            "ts_code": str(row["ts_code"]),
            "signal_date": signal_date,
            "maturity_date": maturity_date,
            "start_quote_date": start_date,
            "end_quote_date": end_date,
            "start_quote_price": (
                start_price if math.isfinite(start_price) else None
            ),
            "end_quote_price": (
                end_price if math.isfinite(end_price) else None
            ),
            "end_quote_staleness_trading_days": staleness,
            "maximum_end_quote_staleness_trading_days": maximum_staleness,
            "stale_price_carried_forward": carried_forward,
            "forward_valuation_only": True,
            "permitted_for_ic_label": False,
            "permitted_for_optimizer_or_comparator_realization": False,
            "price_source": "stock_ohlcv_daily.qfq_close",
            "calendar_source": calendar_sources,
            "start_quote_policy": "last_positive_qfq_on_or_before_signal",
            "end_quote_policy": "last_positive_qfq_on_or_before_maturity",
            "future_data_used": False,
        }
        records.append(audit)
        returns.append(mark_to_market_return)
        statuses.append(status)
        reasons.append(reason)
        start_dates.append(start_date)
        end_dates.append(end_date)
        staleness_values.append(staleness)
        carried_flags.append(carried_forward)
        audit_json.append(json.dumps(
            _canonical(audit), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ))

    frame["benchmark_mark_to_market_return"] = returns
    frame["benchmark_mtm_status"] = statuses
    frame["benchmark_mtm_reason"] = reasons
    frame["benchmark_mtm_start_quote_date"] = start_dates
    frame["benchmark_mtm_end_quote_date"] = end_dates
    frame["benchmark_mtm_end_staleness_trading_days"] = staleness_values
    frame["benchmark_mtm_forward_valuation_only"] = True
    frame["benchmark_mtm_stale_price_carried_forward"] = carried_flags
    frame["benchmark_mtm_audit_json"] = audit_json
    status_counts = {
        status: statuses.count(status) for status in sorted(set(statuses))
    }
    valid_staleness = [value for value in staleness_values if value is not None]
    summary = {
        "status": "ready" if status_counts.get("blocked", 0) == 0 else "partial",
        "rows": len(frame),
        "status_counts": status_counts,
        "maximum_end_quote_staleness_trading_days": maximum_staleness,
        "maximum_observed_end_quote_staleness_trading_days": (
            max(valid_staleness) if valid_staleness else None
        ),
        "stale_price_carried_forward_rows": int(sum(carried_flags)),
        "forward_valuation_only": True,
        "permitted_for_ic_label": False,
        "permitted_for_optimizer_or_comparator_realization": False,
        "price_source": "stock_ohlcv_daily.qfq_close",
        "calendar_source": calendar_sources,
        "blocked_reasons": dict(sorted({
            reason: reasons.count(reason) for reason in reasons if reason
        }.items())),
    }
    return frame, summary


def _attach_official_index_returns(
    panel: pd.DataFrame,
    *,
    config: CSI500StrategyConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach one authoritative index return to every row of each period."""

    configured = str(
        config.official_index_database_path
        or os.getenv("SUBJECT_DATABASE_DB", "")
        or os.getenv("SUBJECT_DB_PATH", "")
    ).strip()
    candidates = [
        configured,
        "G:/subject/main/database/database.db",
        "F:/data/agent_console_private/database.db",
    ]
    source_path = next(
        (Path(value).expanduser().resolve() for value in candidates
         if value and Path(value).expanduser().is_file()),
        None,
    )
    if source_path is None:
        return panel.copy(), {
            "status": "blocked",
            "reason": "official_index_database_missing",
            "index_code": config.index_code,
        }
    try:
        with sqlite3.connect(
            "file:" + source_path.as_posix() + "?mode=ro",
            uri=True,
            timeout=30.0,
        ) as source:
            columns = {
                str(row[1]) for row in source.execute(
                    "pragma table_info(index_market_daily)"
                ).fetchall()
            }
            required = {"trade_date", "ts_code", "open", "close", "source"}
            if not required.issubset(columns):
                return panel.copy(), {
                    "status": "blocked",
                    "reason": "official_index_table_or_columns_missing",
                    "missing_columns": sorted(required - columns),
                    "database_path": str(source_path),
                }
            start = str(panel["signal_date"].min())
            end = str(panel["maturity_date"].max())
            rows = source.execute(
                """
                select trade_date, open, close, source
                from index_market_daily
                where ts_code=? and trade_date>? and trade_date<=?
                order by trade_date
                """,
                (config.index_code, start, end),
            ).fetchall()
    except sqlite3.Error as exc:
        return panel.copy(), {
            "status": "blocked",
            "reason": f"official_index_read_failed:{type(exc).__name__}:{exc}",
            "database_path": str(source_path),
        }
    daily = pd.DataFrame(rows, columns=[
        "trade_date", "open", "close", "source"
    ])
    if daily.empty:
        return panel.copy(), {
            "status": "blocked",
            "reason": "official_index_series_empty",
            "database_path": str(source_path),
        }
    daily["trade_date"] = daily["trade_date"].map(_date_text)
    daily["open"] = pd.to_numeric(daily["open"], errors="coerce")
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
    frame = panel.copy()
    frame["official_index_return"] = np.nan
    frame["official_index_source"] = ""
    periods: list[dict[str, Any]] = []
    for (signal, maturity), indexes in frame.groupby(
        ["signal_date", "maturity_date"], sort=True
    ).groups.items():
        window = daily[
            (daily["trade_date"] > str(signal))
            & (daily["trade_date"] <= str(maturity))
        ]
        entry = window[
            np.isfinite(window["open"]) & (window["open"] > 0.0)
        ].head(1)
        exit_frame = window[
            np.isfinite(window["close"]) & (window["close"] > 0.0)
        ].tail(1)
        if entry.empty or exit_frame.empty:
            periods.append({
                "signal_date": str(signal),
                "maturity_date": str(maturity),
                "status": "blocked",
                "reason": "official_index_entry_or_exit_missing",
            })
            continue
        entry_row = entry.iloc[0]
        exit_row = exit_frame.iloc[0]
        if str(entry_row["trade_date"]) > str(exit_row["trade_date"]):
            periods.append({
                "signal_date": str(signal),
                "maturity_date": str(maturity),
                "status": "blocked",
                "reason": "official_index_entry_after_exit",
            })
            continue
        period_return = (
            float(exit_row["close"]) / float(entry_row["open"]) - 1.0
        )
        sources = sorted({
            str(value).strip() for value in window["source"]
            if value is not None and str(value).strip()
        })
        frame.loc[indexes, "official_index_return"] = period_return
        frame.loc[indexes, "official_index_source"] = ",".join(sources)
        periods.append({
            "signal_date": str(signal),
            "maturity_date": str(maturity),
            "status": "ready",
            "entry_trade_date": str(entry_row["trade_date"]),
            "exit_trade_date": str(exit_row["trade_date"]),
            "return": period_return,
            "sources": sources,
        })
    blocked = [item for item in periods if item["status"] != "ready"]
    return frame, {
        "status": "ready" if not blocked else "partial",
        "index_code": config.index_code,
        "database_path": str(source_path),
        "table": "index_market_daily",
        "execution_alignment": (
            "first_index_open_after_signal_to_last_index_close_by_maturity"
        ),
        "period_count": len(periods),
        "ready_period_count": len(periods) - len(blocked),
        "blocked_periods": blocked,
        "sources": sorted({
            source for item in periods for source in item.get("sources", [])
        }),
        "future_beyond_maturity_used": False,
    }


def _complete_missing_csi500_members(
    connection: sqlite3.Connection,
    panel: pd.DataFrame,
    authoritative: pd.DataFrame,
    *,
    ordered_signal_dates: Sequence[str],
    maturity_by_signal: Mapping[str, str],
    config: CSI500StrategyConfig,
) -> tuple[pd.DataFrame, dict[str, list[dict[str, Any]]]]:
    """Carry one exact prior-month factor row under an explicit PIT contract."""

    frame = panel.copy()
    frame["pit_member_source"] = "exact_signal_panel"
    frame["pit_factor_source_signal_date"] = frame["signal_date"]
    frame["pit_latest_pre_signal_quote_date"] = frame["signal_date"]
    frame["pit_latest_pre_signal_quote_price"] = (
        pd.to_numeric(frame["px"], errors="coerce")
        if "px" in frame.columns else np.nan
    )
    frame["pit_quote_staleness_trading_days"] = 0
    frame["pit_factor_staleness_periods"] = 0
    frame["pit_nontradable"] = 0
    frame["alpha_view_observed"] = 1
    frame["pit_label_source"] = "shared_builder_actual_post_signal_prices"
    frame["pit_label_entry_date"] = (
        frame["entry_trade_date"].map(_optional_date_text)
        if "entry_trade_date" in frame.columns else ""
    )
    frame["pit_label_exit_date"] = (
        frame["exit_trade_date"].map(_optional_date_text)
        if "exit_trade_date" in frame.columns else ""
    )
    frame["pit_source_audit_json"] = ""
    if "buy_limit_weight" not in frame.columns:
        frame["buy_limit_weight"] = np.nan
    if "sell_limit_weight" not in frame.columns:
        frame["sell_limit_weight"] = np.nan

    exact_index = frame.set_index(["signal_date", "ts_code"], drop=False)
    signals = list(map(_date_text, ordered_signal_dates))
    previous_by_signal = {
        signal: signals[index - 1] if index > 0 else None
        for index, signal in enumerate(signals)
    }
    records_by_signal: dict[str, list[dict[str, Any]]] = {
        signal: [] for signal in signals
    }
    recovered_rows: list[dict[str, Any]] = []
    expected_by_signal = {
        signal: set(
            authoritative.loc[
                authoritative["signal_date"] == signal, "ts_code"
            ].astype(str)
        )
        for signal in signals
    }
    observed_by_signal = {
        signal: set(
            frame.loc[frame["signal_date"] == signal, "ts_code"].astype(str)
        )
        for signal in signals
    }
    initial_missing = {
        signal: sorted(expected_by_signal[signal] - observed_by_signal[signal])
        for signal in signals
    }
    if any(initial_missing.values()):
        _validate_missing_member_pit_tables(connection)

    for signal in signals:
        maturity = _date_text(maturity_by_signal[signal])
        previous_signal = previous_by_signal[signal]
        weights = authoritative.loc[
            authoritative["signal_date"] == signal
        ].set_index("ts_code")["benchmark_weight"]
        for code in initial_missing[signal]:
            record: dict[str, Any] = {
                "signal_date": signal,
                "maturity_date": maturity,
                "ts_code": code,
                "status": "blocked",
                "feature_policy": "immediate_previous_month_exact_panel_row",
                "factor_staleness_periods": None,
                "maximum_factor_staleness_periods": int(
                    config.missing_member_max_factor_staleness_periods
                ),
                "buy_limit_weight": 0.0,
                "sell_limit_weight": 0.0,
                "nontradable": True,
                "cross_sectional_imputation_used": False,
                "future_feature_data_used": False,
            }
            if not config.allow_no_alpha_view_missing_members:
                if previous_signal is None:
                    record["reason"] = "missing_previous_month_factor_row"
                    records_by_signal[signal].append(record)
                    continue
                key = (previous_signal, code)
                if key not in exact_index.index:
                    record.update({
                        "reason": "missing_previous_month_factor_row",
                        "factor_source_signal_date": previous_signal,
                    })
                    records_by_signal[signal].append(record)
                    continue
                source = exact_index.loc[key]
                quote_audit = _latest_pre_signal_quote_evidence(
                    connection, ts_code=code, signal_date=signal,
                    max_staleness_trading_days=(
                        config.missing_member_max_quote_staleness_trading_days
                    ),
                )
                label_audit = _post_signal_label_evidence(
                    connection, ts_code=code, signal_date=signal,
                    maturity_date=maturity,
                )
                record.update({
                    "quote_evidence": quote_audit,
                    "label_evidence": label_audit,
                })
                if quote_audit["status"] != "ready":
                    record["reason"] = quote_audit["reason"]
                    records_by_signal[signal].append(record)
                    continue
                if label_audit["status"] != "ready":
                    record["reason"] = label_audit["reason"]
                    records_by_signal[signal].append(record)
                    continue
                record.update({
                    "status": "carried_nontradable",
                    "reason": None,
                    "factor_source_signal_date": previous_signal,
                    "factor_staleness_periods": 1,
                    "latest_pre_signal_quote_date": quote_audit["quote_date"],
                    "quote_staleness_trading_days": quote_audit[
                        "staleness_trading_days"
                    ],
                    "label_backfill_used": False,
                    "label_future_beyond_maturity_used": False,
                })
                carried = source.to_dict()
                carried.update({
                    "trade_date": signal, "signal_date": signal,
                    "maturity_date": maturity, "ts_code": code,
                    "benchmark_weight": _finite(
                        weights.loc[code], "carried_benchmark_weight"
                    ),
                    "label_next_ret": label_audit["label_next_ret"],
                    "entry_trade_date": label_audit["entry_trade_date"],
                    "exit_trade_date": label_audit["exit_trade_date"],
                    "execution_eligible": True,
                    "is_suspended": 1,
                    "suspend_timing": "PIT_CARRIED_NONTRADABLE",
                    "buy_limit_weight": 0.0,
                    "sell_limit_weight": 0.0,
                    "pit_member_source": "previous_month_exact_factor_carry",
                    "pit_factor_source_signal_date": previous_signal,
                    "pit_latest_pre_signal_quote_date": quote_audit["quote_date"],
                    "pit_quote_staleness_trading_days": quote_audit[
                        "staleness_trading_days"
                    ],
                    "pit_factor_staleness_periods": 1,
                    "pit_nontradable": 1,
                    "alpha_view_observed": 1,
                    "pit_label_source": (
                        "actual_post_signal_entry_to_actual_exit_by_maturity"
                    ),
                    "pit_label_entry_date": label_audit["entry_trade_date"],
                    "pit_label_exit_date": label_audit["exit_trade_date"],
                    "pit_source_audit_json": json.dumps(
                        _canonical(record), ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"),
                    ),
                })
                recovered_rows.append(carried)
                records_by_signal[signal].append(record)
                continue
            industry_row = connection.execute(
                """
                select industry_name
                from sw_l1_industry_daily
                where ts_code=? and start_date<=?
                  and (end_date is null or end_date>=?)
                order by start_date desc
                limit 1
                """,
                (code, signal, signal),
            ).fetchone()
            industry = (
                str(industry_row[0]).strip()
                if industry_row is not None and str(industry_row[0]).strip()
                else "UNCLASSIFIED"
            )
            record.update({
                "status": "no_alpha_view_buy_blocked",
                "reason": None,
                "feature_policy": (
                    "missing_signal_quote_means_no_alpha_view; "
                    "risk_exposure_is_cross_section_neutral_and_new_buy_is_zero"
                ),
                "industry": industry,
                "factor_staleness_periods": None,
                "label_policy": "not_used_for_ic_or_new_position_realization",
                "buy_limit_weight": 0.0,
                "sell_limit_weight": 1.0,
                "nontradable": False,
            })
            no_view = {column: np.nan for column in frame.columns}
            no_view.update({
                "trade_date": signal,
                "signal_date": signal,
                "maturity_date": maturity,
                "ts_code": code,
                "industry": industry,
                "industry_name": industry,
                "benchmark_weight": _finite(
                    weights.loc[code], "no_view_benchmark_weight"
                ),
                "label_next_ret": np.nan,
                "execution_eligible": True,
                "is_suspended": 0,
                "suspend_timing": "PIT_NO_ALPHA_VIEW_BUY_BLOCKED",
                "buy_limit_weight": 0.0,
                "sell_limit_weight": 1.0,
                "pit_member_source": "authoritative_index_no_alpha_view",
                "pit_factor_source_signal_date": "",
                "pit_latest_pre_signal_quote_date": "",
                "pit_latest_pre_signal_quote_price": np.nan,
                "pit_quote_staleness_trading_days": np.nan,
                "pit_factor_staleness_periods": np.nan,
                "pit_nontradable": 0,
                "alpha_view_observed": 0,
                "pit_label_source": "none_no_alpha_view",
                "pit_label_entry_date": "",
                "pit_label_exit_date": "",
                "pit_source_audit_json": json.dumps(
                    _canonical(record), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ),
            })
            recovered_rows.append(no_view)
            records_by_signal[signal].append(record)
            continue
            if previous_signal is None:
                record["reason"] = "missing_previous_month_factor_row"
                records_by_signal[signal].append(record)
                continue
            key = (previous_signal, code)
            if key not in exact_index.index:
                record.update({
                    "reason": "missing_previous_month_factor_row",
                    "factor_source_signal_date": previous_signal,
                })
                records_by_signal[signal].append(record)
                continue
            source = exact_index.loc[key]
            if isinstance(source, pd.DataFrame):
                raise CSI500DataContractError(
                    f"duplicate_previous_month_factor_row:{previous_signal}:{code}"
                )
            nonfinite_factors = [
                factor for factor in config.factor_columns
                if factor not in source.index
                or not math.isfinite(float(pd.to_numeric(source[factor], errors="coerce")))
            ]
            if nonfinite_factors:
                record.update({
                    "reason": "previous_month_factor_row_nonfinite",
                    "factor_source_signal_date": previous_signal,
                    "nonfinite_factors": nonfinite_factors,
                })
                records_by_signal[signal].append(record)
                continue

            quote_audit = _latest_pre_signal_quote_evidence(
                connection,
                ts_code=code,
                signal_date=signal,
                max_staleness_trading_days=(
                    config.missing_member_max_quote_staleness_trading_days
                ),
            )
            record["quote_evidence"] = quote_audit
            if quote_audit["status"] != "ready":
                record["reason"] = quote_audit["reason"]
                record["factor_source_signal_date"] = previous_signal
                records_by_signal[signal].append(record)
                continue

            label_audit = _post_signal_label_evidence(
                connection,
                ts_code=code,
                signal_date=signal,
                maturity_date=maturity,
            )
            record["label_evidence"] = label_audit
            if label_audit["status"] != "ready":
                record["reason"] = label_audit["reason"]
                record["factor_source_signal_date"] = previous_signal
                records_by_signal[signal].append(record)
                continue

            record.update({
                "status": "carried_nontradable",
                "reason": None,
                "factor_source_signal_date": previous_signal,
                "factor_staleness_periods": 1,
                "latest_pre_signal_quote_date": quote_audit["quote_date"],
                "quote_staleness_trading_days": quote_audit[
                    "staleness_trading_days"
                ],
                "label_entry_trade_date": label_audit["entry_trade_date"],
                "label_exit_trade_date": label_audit["exit_trade_date"],
                "label_backfill_used": False,
                "label_future_beyond_maturity_used": False,
            })
            carried = source.to_dict()
            carried.update({
                "trade_date": signal,
                "signal_date": signal,
                "maturity_date": maturity,
                "ts_code": code,
                "benchmark_weight": _finite(
                    weights.loc[code], "carried_benchmark_weight"
                ),
                "label_next_ret": label_audit["label_next_ret"],
                "label_close_to_close_ret": np.nan,
                "entry_trade_date": label_audit["entry_trade_date"],
                "exit_trade_date": label_audit["exit_trade_date"],
                "px_entry": label_audit["entry_price"],
                "px_next": label_audit["exit_price"],
                "execution_eligible": True,
                "execution_delay_days": (
                    pd.Timestamp(label_audit["entry_trade_date"])
                    - pd.Timestamp(signal)
                ).days,
                "exit_staleness_days": (
                    pd.Timestamp(maturity)
                    - pd.Timestamp(label_audit["exit_trade_date"])
                ).days,
                "is_suspended": 1,
                "suspend_timing": "PIT_CARRIED_NONTRADABLE",
                "limit_pressure": 0.0,
                "buy_limit_weight": 0.0,
                "sell_limit_weight": 0.0,
                "pit_member_source": "previous_month_exact_factor_carry",
                "pit_factor_source_signal_date": previous_signal,
                "pit_latest_pre_signal_quote_date": quote_audit["quote_date"],
                "pit_latest_pre_signal_quote_price": quote_audit[
                    "quote_qfq_close"
                ],
                "pit_quote_staleness_trading_days": quote_audit[
                    "staleness_trading_days"
                ],
                "pit_factor_staleness_periods": 1,
                "pit_nontradable": 1,
                "pit_label_source": (
                    "actual_post_signal_entry_to_actual_exit_by_maturity"
                ),
                "pit_label_entry_date": label_audit["entry_trade_date"],
                "pit_label_exit_date": label_audit["exit_trade_date"],
                "pit_source_audit_json": json.dumps(
                    _canonical(record), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ),
            })
            recovered_rows.append(carried)
            records_by_signal[signal].append(record)

    if recovered_rows:
        frame = pd.concat(
            [frame, pd.DataFrame(recovered_rows)],
            ignore_index=True,
            sort=False,
        )
    if "industry_name" in frame.columns:
        source_industry = frame["industry_name"].fillna("").astype(str).str.strip()
        target_industry = frame.get(
            "industry", pd.Series("", index=frame.index)
        ).fillna("").astype(str).str.strip()
        frame["industry"] = target_industry.where(
            target_industry.ne(""), source_industry
        )
    return (
        frame.sort_values(
            ["signal_date", "ts_code"], kind="mergesort"
        ).reset_index(drop=True),
        records_by_signal,
    )


def build_csi500_panel_from_database(
    connection: sqlite3.Connection,
    *,
    start: str,
    end: str,
    max_months: int | None = None,
    config: CSI500StrategyConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build a complete exact-membership panel under a strict PIT carry policy."""

    config = config or CSI500StrategyConfig()
    _validate_config(config)
    from framework.backtest.run_v2_models import build_stock_panel
    from model.llm_factor_mining import factor_miner

    membership = load_csi500_constituents(
        connection, start=start, end=end, config=config
    )
    panel, reason = build_stock_panel(
        connection, factor_miner, config.universe,
        _date_text(start), _date_text(end), max_months,
    )
    if panel is None:
        raise CSI500DataContractError(f"build_stock_panel_blocked:{reason}")
    bayesian_responsive_alpha_audit: dict[str, Any] | None = None
    if BAYESIAN_RESPONSIVE_SATELLITE_SCORE in set(config.factor_columns):
        try:
            from framework.backtest.run_v2_models import (
                add_ic_learned_alpha,
                add_v11_alpha_scores,
                add_walkforward_ic_alpha,
            )
            from framework.backtest.index_regime_core_satellite import (
                BayesianAlphaConfig,
                add_bayesian_regime_alpha,
            )
            panel, static_ic_audit = add_ic_learned_alpha(panel)
            panel, walkforward_ic_audit = add_walkforward_ic_alpha(panel)
            panel = add_v11_alpha_scores(panel)
            panel, bayesian_responsive_alpha_audit = add_bayesian_regime_alpha(
                panel,
                BayesianAlphaConfig(
                    horizons=(6, 12, 24, 36),
                    horizon_weights=(0.42, 0.30, 0.18, 0.10),
                    prior_strength=7.0,
                    covariance_lookback=30,
                    covariance_shrinkage=0.50,
                    factor_transition_penalty=1.5,
                ),
                score_column=BAYESIAN_RESPONSIVE_SATELLITE_SCORE,
                confidence_column=BAYESIAN_RESPONSIVE_SATELLITE_CONFIDENCE,
            )
            bayesian_responsive_alpha_audit = {
                **dict(bayesian_responsive_alpha_audit or {}),
                "static_ic_source": {
                    "model": static_ic_audit.get("model"),
                    "factor_count": len(static_ic_audit.get("factor_stats") or {}),
                },
                "walkforward_ic_source": {
                    "lookback": walkforward_ic_audit.get("lookback"),
                    "min_obs": walkforward_ic_audit.get("min_obs"),
                    "fallback_count": walkforward_ic_audit.get("fallback_count"),
                },
                "research_status": "post_test_diagnostic_alpha_component",
                "promotion_gate": "requires_train_validation_reselection_before_production",
                "future_data_used": False,
            }
        except Exception as exc:
            raise CSI500DataContractError(
                "bayesian_responsive_alpha_generation_failed:"
                f"{type(exc).__name__}:{exc}"
            ) from exc
    if config.factor_weight_method == "walkforward_positive_ic":
        missing_champion_factors = sorted(
            set(config.factor_columns) - set(panel.columns)
        )
        if missing_champion_factors:
            raise CSI500DataContractError(
                "walkforward_champion_panel_missing_factors:"
                + ",".join(missing_champion_factors)
            )

    pairs = list(
        factor_miner.month_dates(
            connection, _date_text(start), _date_text(end)
        )
    )
    if max_months:
        pairs = pairs[:max_months]
    if not pairs:
        raise CSI500DataContractError("no_monthly_maturity_pairs")
    ordered_signal_dates = [_date_text(signal) for signal, _ in pairs]
    maturity_by_signal = {
        _date_text(signal): _date_text(maturity)
        for signal, maturity in pairs
    }

    panel = panel.copy()
    panel["signal_date"] = panel["trade_date"].map(_date_text)
    panel["maturity_date"] = panel["signal_date"].map(maturity_by_signal)
    if panel["maturity_date"].isna().any():
        missing_dates = sorted(
            panel.loc[
                panel["maturity_date"].isna(), "signal_date"
            ].unique()
        )
        raise CSI500DataContractError(
            "missing_monthly_maturity_dates:" + ",".join(missing_dates)
        )

    authoritative = membership[
        membership["signal_date"].isin(ordered_signal_dates)
    ][["signal_date", "ts_code", "benchmark_weight"]].copy()
    panel = panel.drop(
        columns=["benchmark_weight", "index_weight"], errors="ignore"
    )
    panel = panel.merge(
        authoritative,
        on=["signal_date", "ts_code"],
        how="inner",
        validate="one_to_one",
    )
    initial_observed_by_signal = {
        signal: set(
            panel.loc[panel["signal_date"] == signal, "ts_code"].astype(str)
        )
        for signal in ordered_signal_dates
    }
    panel, completion_records = _complete_missing_csi500_members(
        connection,
        panel,
        authoritative,
        ordered_signal_dates=ordered_signal_dates,
        maturity_by_signal=maturity_by_signal,
        config=config,
    )
    panel, official_index_audit = _attach_official_index_returns(
        panel, config=config
    )
    if official_index_audit["status"] == "ready":
        benchmark_mtm_audit = {
            "status": "not_used",
            "reason": "official_index_return_available",
        }
    elif config.require_official_index_benchmark:
        raise CSI500DataContractError(
            "official_index_benchmark_required:"
            + str(official_index_audit.get("reason") or "period_incomplete")
        )
    else:
        panel, benchmark_mtm_audit = _attach_benchmark_mark_to_market_returns(
            connection, panel, config=config
        )
    panel, beta_exposure_audit = _attach_database_beta_exposure(
        connection, panel, config=config
    )

    period_audit: list[dict[str, Any]] = []
    for signal_date in ordered_signal_dates:
        expected = set(
            authoritative.loc[
                authoritative["signal_date"] == signal_date, "ts_code"
            ].astype(str)
        )
        initial_observed = initial_observed_by_signal[signal_date]
        observed = set(
            panel.loc[
                panel["signal_date"] == signal_date, "ts_code"
            ].astype(str)
        )
        records = completion_records[signal_date]
        carried = sorted(
            record["ts_code"]
            for record in records
            if record["status"] == "carried_nontradable"
        )
        unrecoverable = [
            record for record in records if record["status"] == "blocked"
        ]
        status = "ready" if expected == observed else "blocked"
        period_audit.append({
            "signal_date": signal_date,
            "maturity_date": maturity_by_signal[signal_date],
            "expected_members": len(expected),
            "exact_signal_panel_members": len(initial_observed),
            "initial_missing_members": sorted(expected - initial_observed),
            "carried_nontradable_members": carried,
            "carried_nontradable_member_count": len(carried),
            "panel_members": len(observed),
            "missing_members": sorted(expected - observed),
            "extra_members": sorted(observed - expected),
            "unrecoverable_members": unrecoverable,
            "member_source_audit": records,
            "buy_sell_limits_zero_for_carried": True,
            "status": status,
        })
    all_complete = bool(period_audit) and all(
        item["status"] == "ready" for item in period_audit
    )
    audit = {
        "universe": config.universe,
        "index_code": config.index_code,
        "uses_build_stock_panel": True,
        "benchmark_mark_to_market": benchmark_mtm_audit,
        "official_index_benchmark": official_index_audit,
        "beta_exposure": beta_exposure_audit,
        "bayesian_responsive_alpha": bayesian_responsive_alpha_audit,
        "member_completion_policy": {
            "membership_source": "exact_signal_date_index_constituents",
            "exact_row_source": "shared_build_stock_panel",
            "missing_row_feature_source": (
                "immediate_previous_month_exact_calculated_panel_row_only"
            ),
            "maximum_factor_staleness_periods": int(
                config.missing_member_max_factor_staleness_periods
            ),
            "latest_quote_must_be_on_or_before_signal": True,
            "maximum_quote_staleness_trading_days": int(
                config.missing_member_max_quote_staleness_trading_days
            ),
            "cross_sectional_imputation_used": False,
            "recursive_factor_carry_used": False,
            "carried_member_trading_policy": (
                "nontradable_with_buy_and_sell_limit_weight_zero"
            ),
            "no_alpha_view_trading_policy": (
                "benchmark_member_without_alpha_view_has_buy_limit_zero_"
                "and_full_sell_capacity; not treated as suspension"
            ),
            "label_policy": (
                "actual_post_signal_tradable_entry_to_actual_exit_by_maturity"
            ),
            "label_backfill_used": False,
            "label_future_beyond_maturity_used": False,
            "insufficient_evidence_policy": (
                "benchmark_only_nontradable_row" if (
                    config.allow_no_alpha_view_missing_members
                ) else "period_blocked"
            ),
        },
        "periods": period_audit,
        "all_periods_exact": all_complete,
        "all_periods_member_complete": all_complete,
        "all_periods_exact_signal_rows": bool(period_audit) and all(
            item["carried_nontradable_member_count"] == 0
            and item["status"] == "ready"
            for item in period_audit
        ),
    }
    return panel, audit


def _robust_z(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    median = float(numeric.median())
    mad = float((numeric - median).abs().median())
    scale = 1.4826 * mad
    if not math.isfinite(scale) or scale <= 1.0e-12:
        scale = float(numeric.std(ddof=0))
    if not math.isfinite(scale) or scale <= 1.0e-12:
        return pd.Series(0.0, index=values.index, dtype=float)
    return ((numeric - median) / scale).clip(-8.0, 8.0)


def _beta_prior_shrink(
    raw_beta: float,
    observations: int,
    config: CSI500StrategyConfig,
) -> float:
    prior = float(config.beta_prior_value)
    obs = max(0, int(observations))
    strength = max(float(config.beta_prior_observations), 0.0)
    shrink = obs / (obs + strength) if obs + strength > 0.0 else 1.0
    lower, upper = config.beta_clip_bounds
    beta = prior + shrink * (float(raw_beta) - prior)
    return float(np.clip(beta, float(lower), float(upper)))


def _rolling_beta_from_pairs(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    *,
    min_observations: int,
    config: CSI500StrategyConfig,
) -> tuple[float, int, str]:
    aligned = pd.concat(
        [
            pd.to_numeric(asset_returns, errors="coerce"),
            pd.to_numeric(benchmark_returns, errors="coerce"),
        ],
        axis=1,
    ).dropna()
    aligned = aligned[np.isfinite(aligned.to_numpy(dtype=float)).all(axis=1)]
    observations = int(len(aligned))
    if observations < int(min_observations):
        return float(config.beta_prior_value), observations, "prior_insufficient_history"
    asset = aligned.iloc[:, 0].to_numpy(dtype=float)
    benchmark = aligned.iloc[:, 1].to_numpy(dtype=float)
    benchmark_variance = float(np.var(benchmark, ddof=1))
    if not math.isfinite(benchmark_variance) or benchmark_variance <= 1.0e-12:
        return float(config.beta_prior_value), observations, "prior_benchmark_variance_too_low"
    covariance = float(np.cov(asset, benchmark, ddof=1)[0, 1])
    if not math.isfinite(covariance):
        return float(config.beta_prior_value), observations, "prior_covariance_nonfinite"
    return (
        _beta_prior_shrink(covariance / benchmark_variance, observations, config),
        observations,
        "ready",
    )


def _derive_monthly_beta_proxy(
    panel: pd.DataFrame,
    config: CSI500StrategyConfig,
) -> tuple[pd.Series, dict[str, Any]]:
    """Derive a strict point-in-time beta proxy from matured monthly rows."""

    required = {"signal_date", "maturity_date", "ts_code", "benchmark_weight", "label_next_ret"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise CSI500DataContractError(
            "monthly_beta_proxy_missing_columns:" + ",".join(missing)
        )
    frame = panel.copy()
    weights = pd.to_numeric(frame["benchmark_weight"], errors="coerce")
    returns = pd.to_numeric(frame["label_next_ret"], errors="coerce")
    benchmark_returns: dict[str, float] = {}
    for signal_date, group in frame.assign(
        _weight=weights, _return=returns,
    ).groupby("signal_date", sort=True):
        valid = group[["_weight", "_return"]].dropna()
        valid = valid[np.isfinite(valid.to_numpy(dtype=float)).all(axis=1)]
        total = float(valid["_weight"].sum()) if not valid.empty else math.nan
        if math.isfinite(total) and total > 0.0:
            benchmark_returns[str(signal_date)] = float(
                np.sum(valid["_weight"].to_numpy(dtype=float) * valid["_return"].to_numpy(dtype=float))
                / total
            )
    history = frame[["signal_date", "maturity_date", "ts_code", "label_next_ret"]].copy()
    history["benchmark_return"] = history["signal_date"].map(benchmark_returns)
    history["label_next_ret"] = pd.to_numeric(history["label_next_ret"], errors="coerce")
    history = history.dropna(subset=["label_next_ret", "benchmark_return"])
    history_by_code = {
        str(code): group.sort_values(["maturity_date", "signal_date"], kind="mergesort")
        for code, group in history.groupby("ts_code", sort=False)
    }
    betas = pd.Series(float(config.beta_prior_value), index=frame.index, dtype=float)
    observations = pd.Series(0, index=frame.index, dtype=int)
    statuses: dict[str, int] = {}
    lookback = int(config.beta_lookback_periods)
    for row in frame[["signal_date", "ts_code"]].itertuples(index=True):
        code_history = history_by_code.get(str(row.ts_code))
        if code_history is None or code_history.empty:
            status = "prior_no_history"
            beta = float(config.beta_prior_value)
            obs = 0
        else:
            eligible = code_history[code_history["maturity_date"].astype(str) < str(row.signal_date)]
            eligible = eligible.tail(lookback)
            beta, obs, status = _rolling_beta_from_pairs(
                eligible["label_next_ret"],
                eligible["benchmark_return"],
                min_observations=int(config.beta_min_period_observations),
                config=config,
            )
        betas.loc[row.Index] = beta
        observations.loc[row.Index] = obs
        statuses[status] = statuses.get(status, 0) + 1
    audit = {
        "status": "ready",
        "method": "strict_matured_monthly_benchmark_proxy_beta",
        "lookback_periods": int(config.beta_lookback_periods),
        "minimum_observations": int(config.beta_min_period_observations),
        "prior_beta": float(config.beta_prior_value),
        "prior_observations": float(config.beta_prior_observations),
        "clip_bounds": list(config.beta_clip_bounds),
        "future_data_used": False,
        "status_counts": statuses,
        "mean_observations": float(observations.mean()) if len(observations) else 0.0,
    }
    return betas, audit


def _attach_database_beta_exposure(
    connection: sqlite3.Connection,
    panel: pd.DataFrame,
    config: CSI500StrategyConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach point-in-time daily beta for the beta layer of index enhancement."""

    if not bool(config.beta_exposure_enabled):
        return panel, {"status": "not_requested", "reason": "beta_exposure_disabled"}
    if "style_beta" not in set(map(str, config.style_columns)):
        return panel, {
            "status": "not_requested",
            "reason": "style_beta_not_in_configured_style_columns",
        }
    if "style_beta" in panel.columns:
        return panel, {"status": "provided", "source": "input_panel.style_beta"}
    required = {"signal_date", "ts_code", "benchmark_weight"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise CSI500DataContractError(
            "database_beta_panel_missing_columns:" + ",".join(missing)
        )
    frame = panel.copy()
    codes = sorted({str(code) for code in frame["ts_code"].astype(str)})
    signal_dates = sorted({_date_text(value) for value in frame["signal_date"]})
    if not codes or not signal_dates:
        raise CSI500DataContractError("database_beta_empty_codes_or_dates")
    min_signal = pd.to_datetime(signal_dates[0], format="%Y%m%d")
    max_signal = signal_dates[-1]
    start_bound = (
        min_signal - pd.Timedelta(days=max(400, int(config.beta_lookback_trading_days) * 3))
    ).strftime("%Y%m%d")
    chunks: list[pd.DataFrame] = []
    chunk_size = 900
    for start_index in range(0, len(codes), chunk_size):
        chunk = codes[start_index:start_index + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        query = (
            "select trade_date, ts_code, qfq_close from stock_ohlcv_daily "
            f"where ts_code in ({placeholders}) and trade_date>=? and trade_date<? "
            "order by trade_date, ts_code"
        )
        chunks.append(pd.read_sql_query(query, connection, params=[*chunk, start_bound, max_signal]))
    prices = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    if prices.empty:
        raise CSI500DataContractError("database_beta_price_history_empty")
    prices["trade_date"] = prices["trade_date"].map(_date_text)
    prices["ts_code"] = prices["ts_code"].astype(str)
    prices["qfq_close"] = pd.to_numeric(prices["qfq_close"], errors="coerce")
    prices = prices.dropna(subset=["qfq_close"])
    prices = prices[prices["qfq_close"] > 0.0]
    wide_prices = prices.pivot_table(
        index="trade_date", columns="ts_code", values="qfq_close", aggfunc="last"
    ).sort_index()
    daily_returns = wide_prices.pct_change(fill_method=None)
    betas = pd.Series(float(config.beta_prior_value), index=frame.index, dtype=float)
    obs_values = pd.Series(0, index=frame.index, dtype=int)
    status_counts: dict[str, int] = {}
    period_audit: list[dict[str, Any]] = []
    lookback = int(config.beta_lookback_trading_days)
    min_obs = int(config.beta_min_trading_observations)
    for signal_date, group in frame.groupby("signal_date", sort=True):
        date_text = _date_text(signal_date)
        historical = daily_returns[daily_returns.index < date_text].tail(lookback)
        period_status_counts: dict[str, int] = {}
        if historical.empty:
            period_audit.append({
                "signal_date": date_text,
                "status": "prior_no_daily_history",
                "rows": int(len(group)),
                "historical_days": 0,
                "ready_count": 0,
            })
            continue
        codes_period = group["ts_code"].astype(str).tolist()
        weights = pd.to_numeric(group["benchmark_weight"], errors="coerce").to_numpy(dtype=float)
        weight_total = float(np.nansum(weights))
        if not math.isfinite(weight_total) or weight_total <= 0.0:
            raise CSI500DataContractError(f"database_beta_invalid_benchmark_weights:{date_text}")
        weights = weights / weight_total
        sub = historical.reindex(columns=codes_period)
        finite = np.isfinite(sub.to_numpy(dtype=float))
        available_weight = finite @ weights
        weighted_sum = np.nansum(sub.to_numpy(dtype=float) * weights[None, :], axis=1)
        benchmark = pd.Series(np.nan, index=sub.index, dtype=float)
        usable = available_weight >= 0.75
        benchmark.loc[usable] = weighted_sum[usable] / available_weight[usable]
        ready_count = 0
        for row_index, code in zip(group.index, codes_period):
            beta, obs, status = _rolling_beta_from_pairs(
                sub[code], benchmark,
                min_observations=min_obs,
                config=config,
            )
            betas.loc[row_index] = beta
            obs_values.loc[row_index] = obs
            period_status_counts[status] = period_status_counts.get(status, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "ready":
                ready_count += 1
        period_audit.append({
            "signal_date": date_text,
            "status": "ready",
            "rows": int(len(group)),
            "historical_days": int(len(historical)),
            "benchmark_usable_days": int(np.isfinite(benchmark.to_numpy(dtype=float)).sum()),
            "ready_count": int(ready_count),
            "prior_count": int(len(group) - ready_count),
            "status_counts": period_status_counts,
        })
    frame["style_beta"] = betas
    frame["style_beta_observations"] = obs_values
    audit = {
        "status": "ready",
        "method": "point_in_time_daily_component_benchmark_proxy_beta",
        "price_source": "stock_ohlcv_daily.qfq_close",
        "lookback_trading_days": int(config.beta_lookback_trading_days),
        "minimum_observations": int(config.beta_min_trading_observations),
        "benchmark_proxy": "current_signal_csi500_weights_on_past_component_returns",
        "available_weight_floor": 0.75,
        "prior_beta": float(config.beta_prior_value),
        "prior_observations": float(config.beta_prior_observations),
        "clip_bounds": list(config.beta_clip_bounds),
        "future_data_used": False,
        "rows": int(len(frame)),
        "price_rows": int(len(prices)),
        "price_start": str(wide_prices.index.min()) if len(wide_prices) else None,
        "price_end": str(wide_prices.index.max()) if len(wide_prices) else None,
        "status_counts": status_counts,
        "periods": period_audit,
    }
    return frame, audit


def _derive_style_columns(
    panel: pd.DataFrame,
    config: CSI500StrategyConfig,
) -> pd.DataFrame:
    frame = panel.copy()
    if "style_beta" in config.style_columns and "style_beta" not in frame.columns:
        beta, audit = _derive_monthly_beta_proxy(frame, config)
        frame["style_beta"] = beta
        frame["style_beta_observations"] = audit.get("mean_observations")
    recipes: dict[str, tuple[str, Callable[[pd.Series], pd.Series]]] = {
        "style_size": (
            "total_mv", lambda series: np.log(pd.to_numeric(series, errors="coerce").where(lambda x: x > 0.0)),
        ),
        "style_value": (
            "pb", lambda series: -pd.to_numeric(series, errors="coerce").where(lambda x: x > 0.0),
        ),
        "style_momentum": (
            "mom60", lambda series: pd.to_numeric(series, errors="coerce"),
        ),
        "style_liquidity": (
            "amount", lambda series: np.log(pd.to_numeric(series, errors="coerce").where(lambda x: x > 0.0)),
        ),
        "style_beta": (
            "style_beta", lambda series: pd.to_numeric(series, errors="coerce"),
        ),
    }
    for style in config.style_columns:
        if style in frame.columns:
            frame[style] = pd.to_numeric(frame[style], errors="coerce")
            continue
        if style not in recipes:
            raise CSI500DataContractError(f"no_style_recipe:{style}")
        raw_column, transform = recipes[style]
        if raw_column not in frame.columns:
            raise CSI500DataContractError(
                f"missing_style_and_source:{style}:{raw_column}"
            )
        raw = transform(frame[raw_column])
        frame[style] = raw.groupby(frame["signal_date"]).transform(_robust_z)
    return frame

def _preprocess_style_exposures(
    panel: pd.DataFrame,
    config: CSI500StrategyConfig,
) -> pd.DataFrame:
    """Build finite, point-in-time style exposures with an explicit audit trail."""

    processed: list[pd.DataFrame] = []
    for signal_date, source_group in panel.groupby("signal_date", sort=True):
        group = source_group.copy()
        period_audit: dict[str, Any] = {
            "signal_date": str(signal_date),
            "status": "ready",
            "method": "contemporaneous_cross_sectional_winsor_median_impute_robust_z",
            "point_in_time": True,
            "winsor_quantiles": [0.01, 0.99],
            "fill_method": "contemporaneous_cross_section_median_after_winsorization",
            "zero_fill_used": False,
            "future_data_used": False,
            "columns": {},
        }
        errors: list[str] = []
        for style in config.style_columns:
            numeric = pd.to_numeric(group[style], errors="coerce")
            positive_inf = int(np.isposinf(numeric.to_numpy(dtype=float)).sum())
            negative_inf = int(np.isneginf(numeric.to_numpy(dtype=float)).sum())
            numeric = numeric.replace([np.inf, -np.inf], np.nan)
            finite_mask = np.isfinite(numeric.to_numpy(dtype=float))
            finite_count = int(finite_mask.sum())
            coverage = finite_count / len(group)
            column_audit: dict[str, Any] = {
                "input_count": int(len(group)),
                "finite_input_count": finite_count,
                "missing_input_count": int(len(group) - finite_count),
                "positive_inf_replaced_with_nan": positive_inf,
                "negative_inf_replaced_with_nan": negative_inf,
                "raw_coverage": coverage,
                "required_coverage": config.style_min_coverage,
            }
            if coverage < config.style_min_coverage:
                error = (
                    f"style_coverage_below_threshold:{signal_date}:"
                    f"{style}:{coverage:.6f}"
                )
                errors.append(error)
                column_audit.update({"status": "blocked", "reason": error})
                group[style] = numeric
                period_audit["columns"][style] = column_audit
                continue

            finite_values = numeric.loc[finite_mask]
            lower = float(finite_values.quantile(0.01))
            upper = float(finite_values.quantile(0.99))
            winsorized = numeric.clip(lower=lower, upper=upper)
            clipped_count = int(
                (
                    finite_mask
                    & ~np.isclose(
                        numeric.to_numpy(dtype=float),
                        winsorized.to_numpy(dtype=float),
                        rtol=0.0,
                        atol=0.0,
                        equal_nan=True,
                    )
                ).sum()
            )
            fill_value = float(winsorized.loc[finite_mask].median())
            filled = winsorized.fillna(fill_value)
            center = float(filled.median())
            mad = float((filled - center).abs().median())
            scale = 1.4826 * mad
            scale_method = "mad"
            if not math.isfinite(scale) or scale <= 1.0e-12:
                scale = float(filled.std(ddof=0))
                scale_method = "standard_deviation"
            if not math.isfinite(scale) or scale <= 1.0e-12:
                error = f"style_dispersion_zero:{signal_date}:{style}"
                errors.append(error)
                column_audit.update({
                    "status": "blocked",
                    "reason": error,
                    "winsor_lower": lower,
                    "winsor_upper": upper,
                    "fill_value": fill_value,
                })
                group[style] = filled
                period_audit["columns"][style] = column_audit
                continue

            standardized = ((filled - center) / scale).clip(-8.0, 8.0)
            if not np.isfinite(standardized.to_numpy(dtype=float)).all():
                error = f"style_preprocessing_nonfinite:{signal_date}:{style}"
                errors.append(error)
                column_audit.update({"status": "blocked", "reason": error})
                group[style] = standardized
                period_audit["columns"][style] = column_audit
                continue
            group[style] = standardized
            column_audit.update({
                "status": "ready",
                "winsor_lower": lower,
                "winsor_upper": upper,
                "winsorized_count": clipped_count,
                "fill_value": fill_value,
                "imputed_count": int(len(group) - finite_count),
                "center": center,
                "scale": scale,
                "scale_method": scale_method,
                "postprocess_finite_count": int(
                    np.isfinite(standardized.to_numpy(dtype=float)).sum()
                ),
            })
            period_audit["columns"][style] = column_audit
        if errors:
            period_audit["status"] = "blocked"
            period_audit["reasons"] = errors
        group["style_contract_error"] = "|".join(errors)
        group["style_preprocessing_json"] = json.dumps(
            _canonical(period_audit), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        processed.append(group)
    return pd.concat(processed, ignore_index=True)



def _normalize_panel(
    panel: pd.DataFrame,
    config: CSI500StrategyConfig,
) -> pd.DataFrame:
    if not isinstance(panel, pd.DataFrame) or panel.empty:
        raise CSI500DataContractError("panel_must_be_nonempty_dataframe")
    aliases = {
        "trade_date": "signal_date",
        "industry_name": "industry",
        "index_weight": "benchmark_weight",
    }
    frame = panel.copy()
    for source, target in aliases.items():
        if target not in frame.columns and source in frame.columns:
            frame[target] = frame[source]
    if "maturity_date" not in frame.columns and "exit_trade_date" in frame.columns:
        frame["maturity_date"] = frame["exit_trade_date"]
    required = {
        "signal_date", "maturity_date", "ts_code", "benchmark_weight",
        "industry", "label_next_ret",
    } | set(config.factor_columns)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise CSI500DataContractError("panel_missing_columns:" + ",".join(missing))
    frame["signal_date"] = frame["signal_date"].map(_date_text)
    frame["maturity_date"] = frame["maturity_date"].map(_date_text)
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip()
    frame["industry"] = frame["industry"].fillna("").astype(str).str.strip()
    numeric_columns = {
        "benchmark_weight", "label_next_ret", *config.factor_columns,
        *(
            ["benchmark_mark_to_market_return"]
            if "benchmark_mark_to_market_return" in frame.columns else []
        ),
    }
    numeric_columns.update(
        column for column in (
            "total_mv", "pb", "mom60", "amount", "buy_limit_weight",
            "sell_limit_weight", "is_suspended", "limit_pressure",
        ) if column in frame.columns
    )
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = _derive_style_columns(frame, config)
    observed_alpha = pd.to_numeric(
        frame.get("alpha_view_observed", pd.Series(1, index=frame.index)),
        errors="coerce",
    ).fillna(0).astype(bool)
    for column in config.factor_columns:
        medians = frame[column].where(observed_alpha).groupby(
            frame["signal_date"]
        ).transform("median")
        frame.loc[~observed_alpha, column] = medians.loc[~observed_alpha]
    for column in config.style_columns:
        frame.loc[~observed_alpha, column] = 0.0
    frame = frame.sort_values(["signal_date", "ts_code"], kind="mergesort").reset_index(drop=True)
    if frame.duplicated(["signal_date", "ts_code"]).any():
        raise CSI500DataContractError("duplicate_signal_date_ts_code")
    frame = _preprocess_style_exposures(frame, config)
    normalized_groups: list[pd.DataFrame] = []
    for signal_date, group in frame.groupby("signal_date", sort=True):
        group = group.copy()
        errors = [
            value for value in sorted(set(group["style_contract_error"].astype(str)))
            if value
        ]
        count = len(group)
        weights = group["benchmark_weight"].to_numpy(dtype=float)
        total = float(weights.sum()) if np.isfinite(weights).all() else math.nan
        audit: dict[str, Any] = {
            "signal_date": str(signal_date),
            "member_count": count,
            "expected_members": config.expected_members,
            "input_total": total if math.isfinite(total) else None,
            "normalized": False,
            "missing_member_masked_by_normalization": False,
        }
        if count != config.expected_members:
            error = (
                f"benchmark_member_count_not_{config.expected_members}:"
                f"{signal_date}:{count}"
            )
            errors.append(error)
            audit.update({"status": "blocked", "reason": error})
            group["benchmark_input_total"] = total
            group["benchmark_weight_unit"] = ""
            group["benchmark_weight_normalization_json"] = json.dumps(
                _canonical(audit), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            group["period_contract_error"] = "|".join(errors)
            normalized_groups.append(group)
            continue
        if not np.isfinite(weights).all() or np.any(weights <= 0.0):
            error = f"invalid_benchmark_weights:{signal_date}"
            errors.append(error)
            audit.update({"status": "blocked", "reason": error})
            group["benchmark_input_total"] = total
            group["benchmark_weight_unit"] = ""
            group["benchmark_weight_normalization_json"] = json.dumps(
                _canonical(audit), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            group["period_contract_error"] = "|".join(errors)
            normalized_groups.append(group)
            continue
        fraction_range = (
            (config.raw_index_weight_total - config.raw_index_weight_tolerance)
            / config.raw_index_weight_total,
            (config.raw_index_weight_total + config.raw_index_weight_tolerance)
            / config.raw_index_weight_total,
        )
        percent_range = (
            config.raw_index_weight_total - config.raw_index_weight_tolerance,
            config.raw_index_weight_total + config.raw_index_weight_tolerance,
        )
        if fraction_range[0] <= total <= fraction_range[1]:
            unit = "fraction"
            accepted_range = fraction_range
        elif percent_range[0] <= total <= percent_range[1]:
            unit = "percent"
            accepted_range = percent_range
        else:
            error = f"benchmark_weight_total_invalid:{signal_date}:{total:.12f}"
            errors.append(error)
            audit.update({
                "status": "blocked",
                "reason": error,
                "accepted_fraction_range": list(fraction_range),
                "accepted_percent_range": list(percent_range),
            })
            group["benchmark_input_total"] = total
            group["benchmark_weight_unit"] = ""
            group["benchmark_weight_normalization_json"] = json.dumps(
                _canonical(audit), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            group["period_contract_error"] = "|".join(errors)
            normalized_groups.append(group)
            continue
        group["benchmark_input_total"] = total
        group["benchmark_weight"] = weights / total
        group["benchmark_weight_unit"] = unit
        audit.update({
            "status": "normalized",
            "input_unit": unit,
            "accepted_total_range": list(accepted_range),
            "normalized": True,
            "normalized_total": float(group["benchmark_weight"].sum()),
        })
        group["benchmark_weight_normalization_json"] = json.dumps(
            _canonical(audit), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        group["period_contract_error"] = "|".join(errors)
        normalized_groups.append(group)
    return pd.concat(normalized_groups, ignore_index=True)


def _period_contract(
    group: pd.DataFrame,
    config: CSI500StrategyConfig,
) -> dict[str, Any]:
    signal_date = str(group["signal_date"].iloc[0])
    count = len(group)
    errors = sorted(set(group.get("period_contract_error", pd.Series([""])).astype(str)) - {""})
    if errors:
        raise CSI500DataContractError(f"period_contract:{signal_date}:{'|'.join(errors)}")
    if count < config.minimum_members:
        raise CSI500DataContractError(
            f"period_member_count_below_{config.minimum_members}:{signal_date}:{count}"
        )
    if config.require_exact_members and count != config.expected_members:
        raise CSI500DataContractError(
            f"period_member_count_not_{config.expected_members}:{signal_date}:{count}"
        )
    if group["ts_code"].duplicated().any() or (group["ts_code"] == "").any():
        raise CSI500DataContractError(f"duplicate_or_empty_code:{signal_date}")
    if (group["industry"] == "").any():
        raise CSI500DataContractError(f"missing_industry:{signal_date}")
    maturities = sorted(group["maturity_date"].unique())
    if len(maturities) != 1 or maturities[0] <= signal_date:
        raise CSI500DataContractError(f"invalid_outcome_maturity:{signal_date}")
    total = float(group["benchmark_weight"].sum())
    if abs(total - 1.0) > config.normalized_weight_tolerance:
        raise CSI500DataContractError(f"normalized_weight_sum_not_one:{signal_date}:{total}")
    coverage: dict[str, float] = {}
    for column in config.factor_columns:
        values = pd.to_numeric(group[column], errors="coerce")
        ratio = float(np.isfinite(values).mean())
        coverage[column] = ratio
        if ratio < config.factor_min_coverage:
            raise CSI500DataContractError(
                f"factor_coverage_below_threshold:{signal_date}:{column}:{ratio:.6f}"
            )
    for column in config.style_columns:
        values = pd.to_numeric(group[column], errors="coerce")
        ratio = float(np.isfinite(values).mean())
        coverage[column] = ratio
        if ratio < config.style_min_coverage:
            raise CSI500DataContractError(
                f"style_coverage_below_threshold:{signal_date}:{column}:{ratio:.6f}"
            )
    return {
        "signal_date": signal_date,
        "outcome_maturity_date": maturities[0],
        "member_count": count,
        "weight_sum": total,
        "coverage": coverage,
        "benchmark_weight_normalization": json.loads(
            str(group["benchmark_weight_normalization_json"].iloc[0])
        ),
    }


def _rank_ic(
    values: pd.Series,
    labels: pd.Series,
    minimum: int,
    observed: pd.Series | None = None,
) -> tuple[float | None, int]:
    x = pd.to_numeric(values, errors="coerce")
    y = pd.to_numeric(labels, errors="coerce")
    valid = np.isfinite(x) & np.isfinite(y)
    if observed is not None:
        valid &= pd.to_numeric(observed, errors="coerce").fillna(0).astype(bool)
    count = int(valid.sum())
    if count < minimum:
        return None, count
    x_rank = x.loc[valid].rank(method="average")
    y_rank = y.loc[valid].rank(method="average")
    value = x_rank.corr(y_rank)
    return (float(value), count) if pd.notna(value) else (None, count)


def _exposure_audit(
    group: pd.DataFrame,
    scores: np.ndarray,
    config: CSI500StrategyConfig,
) -> dict[str, Any]:
    benchmark = group["benchmark_weight"].to_numpy(dtype=float)
    industries = group["industry"].astype(str).to_numpy()
    industry_exposure = {
        industry: float(np.sum(benchmark[industries == industry] * scores[industries == industry]))
        for industry in sorted(set(industries))
    }
    style_exposure = {
        style: float(
            np.sum(benchmark * group[style].to_numpy(dtype=float) * scores)
        )
        for style in config.style_columns
    }
    all_values = list(industry_exposure.values()) + list(style_exposure.values())
    return {
        "industry": industry_exposure,
        "style": style_exposure,
        "weighted_mean": float(np.sum(benchmark * scores)),
        "max_abs_exposure": max((abs(value) for value in all_values), default=0.0),
    }


def _neutralize_score(
    group: pd.DataFrame,
    raw_score: np.ndarray,
    config: CSI500StrategyConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    industries = pd.get_dummies(
        group["industry"].astype(str), prefix="industry", dtype=float
    )
    styles = group[list(config.style_columns)].astype(float)
    if not np.isfinite(styles.to_numpy(dtype=float)).all():
        raise CSI500DataContractError("style_exposure_nonfinite_at_neutralization")
    design = np.column_stack([industries.to_numpy(dtype=float), styles.to_numpy(dtype=float)])
    benchmark = group["benchmark_weight"].to_numpy(dtype=float)
    root_weight = np.sqrt(benchmark)
    weighted_design = design * root_weight[:, None]
    weighted_target = raw_score * root_weight
    gram = weighted_design.T @ weighted_design
    penalty = np.eye(gram.shape[0], dtype=float) * config.neutralization_ridge
    beta = np.linalg.pinv(gram + penalty, hermitian=True) @ (
        weighted_design.T @ weighted_target
    )
    residual = raw_score - design @ beta
    residual -= float(np.sum(benchmark * residual))
    dispersion = math.sqrt(float(np.sum(benchmark * np.square(residual))))
    if not math.isfinite(dispersion) or dispersion <= 1.0e-12:
        raise CSI500DataContractError("neutralized_score_has_no_dispersion")
    score = residual / dispersion
    before = _exposure_audit(group, raw_score, config)
    after = _exposure_audit(group, score, config)
    if after["max_abs_exposure"] > config.neutralization_tolerance:
        raise CSI500DataContractError(
            f"neutralization_residual_exceeds_tolerance:{after['max_abs_exposure']:.12g}"
        )
    return score, {
        "method": "benchmark_weighted_industry_style_residual",
        "ridge": config.neutralization_ridge,
        "style_preprocessing": json.loads(
            str(group["style_preprocessing_json"].iloc[0])
        ),
        "before": before,
        "after": after,
    }


def _capped_signed_l1_weights(
    raw_direction: np.ndarray,
    absolute_cap: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Normalise a signed direction to L1 one under a hard absolute cap."""

    raw = np.asarray(raw_direction, dtype=float).reshape(-1)
    if len(raw) == 0 or not np.isfinite(raw).all():
        raise CSI500DataContractError("ic_weight_direction_must_be_finite_nonempty")
    magnitudes = np.abs(raw)
    active = np.flatnonzero(magnitudes > 1.0e-14).tolist()
    if not active:
        raise CSI500DataContractError("ic_weight_direction_has_no_signal")
    cap = float(absolute_cap)
    if len(active) * cap < 1.0 - 1.0e-12:
        raise CSI500DataContractError(
            "nonzero_ic_directions_insufficient_for_absolute_weight_cap"
        )

    uncapped = magnitudes / float(magnitudes.sum())
    allocated = np.zeros(len(raw), dtype=float)
    remaining = 1.0
    available = list(active)
    cap_binding: list[int] = []
    while available:
        denominator = float(magnitudes[available].sum())
        if denominator <= 1.0e-14:
            raise CSI500DataContractError("ic_weight_cap_redistribution_has_no_mass")
        proposed = remaining * magnitudes[available] / denominator
        binding = [
            index for index, value in zip(available, proposed)
            if float(value) > cap + 1.0e-14
        ]
        if not binding:
            allocated[available] = proposed
            remaining = 0.0
            break
        allocated[binding] = cap
        cap_binding.extend(binding)
        remaining -= cap * len(binding)
        available = [index for index in available if index not in set(binding)]
    if abs(float(allocated.sum()) - 1.0) > 1.0e-10:
        raise CSI500DataContractError("capped_ic_absolute_weights_do_not_sum_to_one")
    signed = np.sign(raw) * allocated
    return signed, {
        "uncapped_signed_l1_weights": (np.sign(raw) * uncapped).tolist(),
        "absolute_weight_cap": cap,
        "cap_binding_indexes": sorted(cap_binding),
        "cap_binding_count": len(set(cap_binding)),
        "absolute_weight_sum": float(np.abs(signed).sum()),
        "maximum_absolute_weight": float(np.abs(signed).max()),
        "redistribution_method": "proportional_waterfill_preserve_direction_sign",
    }


def _causal_ic_precision_weights(
    matured: Sequence[Mapping[str, Any]],
    config: CSI500StrategyConfig,
) -> tuple[dict[str, float] | None, dict[str, dict[str, Any]], dict[str, Any]]:
    """Estimate causal factor weights with mean/covariance shrinkage and caps."""

    eligible: list[str] = []
    excluded: dict[str, str] = {}
    for factor in config.factor_columns:
        history = [
            float(event["ic"][factor])
            for event in matured
            if event["ic"].get(factor) is not None
            and math.isfinite(float(event["ic"][factor]))
        ]
        if len(history) >= int(config.factor_min_history):
            eligible.append(factor)
        else:
            excluded[factor] = (
                f"history_below_minimum:{len(history)}<{config.factor_min_history}"
            )

    effective_absolute_cap = max(
        float(config.factor_absolute_weight_cap),
        1.0 / max(len(eligible), 1),
    )
    base_audit: dict[str, Any] = {
        "method": "bayesian_mean_diagonal_covariance_shrinkage_precision_cap",
        "eligible_factors": eligible,
        "excluded_factors": excluded,
        "matured_event_count": len(matured),
        "required_history": int(config.factor_min_history),
        "mean_prior_std": float(config.factor_ic_mean_prior_std),
        "covariance_shrinkage": float(config.factor_ic_covariance_shrinkage),
        "covariance_ridge": float(config.factor_ic_covariance_ridge),
        "requested_absolute_weight_cap": float(
            config.factor_absolute_weight_cap
        ),
        "effective_absolute_weight_cap": effective_absolute_cap,
        "future_data_used": False,
        "hyperparameters_selected_from_report_only_test": False,
        "fallback_used": False,
    }
    if not eligible:

        base_audit.update({
            "status": "warmup",
            "reason": "eligible_factor_count_insufficient_for_weight_cap",
        })
        return None, {}, base_audit

    common_events = [
        event for event in matured
        if all(
            event["ic"].get(factor) is not None
            and math.isfinite(float(event["ic"][factor]))
            for factor in eligible
        )
    ]
    if len(common_events) < int(config.factor_min_history):
        base_audit.update({
            "status": "warmup",
            "reason": "common_ic_history_below_minimum",
            "common_observations": len(common_events),
        })
        return None, {}, base_audit

    matrix = np.asarray(
        [[float(event["ic"][factor]) for factor in eligible]
         for event in common_events],
        dtype=float,
    )
    mean_ic = matrix.mean(axis=0)
    sample_covariance = np.atleast_2d(np.cov(matrix, rowvar=False, ddof=1))
    sample_covariance = (sample_covariance + sample_covariance.T) / 2.0
    sample_variance = np.maximum(np.diag(sample_covariance), 0.0)
    standard_error_squared = sample_variance / float(len(matrix))
    prior_variance = float(config.factor_ic_mean_prior_std) ** 2
    mean_shrinkage = prior_variance / (
        prior_variance + standard_error_squared
    )
    shrunk_mean = mean_ic * mean_shrinkage

    diagonal_target = np.diag(sample_variance)
    covariance_shrinkage = float(config.factor_ic_covariance_shrinkage)
    shrunk_covariance = (
        (1.0 - covariance_shrinkage) * sample_covariance
        + covariance_shrinkage * diagonal_target
    )
    ridge = float(config.factor_ic_covariance_ridge)
    regularized_covariance = (
        shrunk_covariance + np.eye(len(eligible), dtype=float) * ridge
    )
    raw_direction = np.linalg.solve(regularized_covariance, shrunk_mean)
    try:
        signed_weights, cap_audit = _capped_signed_l1_weights(
            raw_direction, effective_absolute_cap
        )
    except CSI500DataContractError as exc:
        base_audit.update({"status": "warmup", "reason": str(exc)})
        return None, {}, base_audit

    weights = {
        factor: float(weight)
        for factor, weight in zip(eligible, signed_weights)
        if abs(float(weight)) > 1.0e-14
    }
    evidence: dict[str, dict[str, Any]] = {}
    for index, factor in enumerate(eligible):
        standard_deviation = math.sqrt(float(sample_variance[index]))
        evidence[factor] = {
            "observations": int(len(matrix)),
            "mean_rank_ic": float(mean_ic[index]),
            "rank_ic_std": standard_deviation,
            "annualized_icir": float(
                mean_ic[index] / max(standard_deviation, 1.0e-6)
                * math.sqrt(config.periods_per_year)
            ),
            "mean_standard_error": math.sqrt(
                float(standard_error_squared[index])
            ),
            "mean_shrinkage_multiplier": float(mean_shrinkage[index]),
            "shrunk_mean_rank_ic": float(shrunk_mean[index]),
            "precision_direction": float(raw_direction[index]),
            "final_weight": float(signed_weights[index]),
        }

    audit = {
        **base_audit,
        "status": "ready",
        "common_observations": int(len(matrix)),
        "factor_order": eligible,
        "event_signal_dates": [str(event["signal_date"]) for event in common_events],
        "event_maturity_dates": [str(event["maturity_date"]) for event in common_events],
        "ic_matrix": matrix.tolist(),
        "ic_matrix_hash": _hash_parts(eligible, matrix),
        "sample_mean": mean_ic.tolist(),
        "mean_standard_error_squared": standard_error_squared.tolist(),
        "mean_shrinkage_multiplier": mean_shrinkage.tolist(),
        "shrunk_mean": shrunk_mean.tolist(),
        "sample_covariance": sample_covariance.tolist(),
        "diagonal_covariance_target": diagonal_target.tolist(),
        "shrunk_covariance": shrunk_covariance.tolist(),
        "regularized_covariance": regularized_covariance.tolist(),
        "regularized_condition_number": float(
            np.linalg.cond(regularized_covariance)
        ),
        "raw_precision_direction": raw_direction.tolist(),
        "final_weights": weights,
        **cap_audit,
    }
    audit["cap_binding_factors"] = [
        eligible[index] for index in cap_audit["cap_binding_indexes"]
    ]
    return weights, evidence, audit


def _causal_adaptive_icir_weights(
    matured: Sequence[Mapping[str, Any]],
    config: CSI500StrategyConfig,
) -> tuple[dict[str, float] | None, dict[str, dict[str, Any]], dict[str, Any]]:
    """Empirical-Bayes ICIR weights using factor-wise causal histories."""

    directions = np.zeros(len(config.factor_columns), dtype=float)
    evidence: dict[str, dict[str, Any]] = {}
    excluded: dict[str, str] = {}
    for index, factor in enumerate(config.factor_columns):
        history = np.asarray([
            float(event["ic"][factor])
            for event in matured
            if event["ic"].get(factor) is not None
            and math.isfinite(float(event["ic"][factor]))
        ], dtype=float)[-int(config.factor_lookback):]
        if len(history) < int(config.factor_min_history):
            excluded[factor] = (
                f"history_below_minimum:{len(history)}<"
                f"{int(config.factor_min_history)}"
            )
            continue
        sample_mean = float(np.mean(history))
        sample_variance = (
            float(np.var(history, ddof=1)) if len(history) > 1 else 0.0
        )
        prior_strength = float(config.factor_evidence_prior_observations)
        posterior_mean = sample_mean * len(history) / (
            len(history) + prior_strength
        )
        posterior_standard_error = math.sqrt(
            sample_variance / max(len(history), 1)
            + float(config.factor_ic_mean_prior_std) ** 2
            / (len(history) + prior_strength)
        )
        raw_evidence = posterior_mean / max(
            posterior_standard_error, 1.0e-8
        )
        directions[index] = float(np.clip(
            raw_evidence,
            -float(config.factor_evidence_clip),
            float(config.factor_evidence_clip),
        ))
        history_std = math.sqrt(max(sample_variance, 0.0))
        evidence[factor] = {
            "observations": int(len(history)),
            "mean_rank_ic": sample_mean,
            "rank_ic_std": history_std,
            "annualized_icir": (
                sample_mean / max(history_std, 1.0e-6)
                * math.sqrt(config.periods_per_year)
            ),
            "posterior_mean_rank_ic": posterior_mean,
            "posterior_standard_error": posterior_standard_error,
            "raw_evidence": raw_evidence,
            "clipped_evidence": float(directions[index]),
        }
    active = np.flatnonzero(np.abs(directions) > 1.0e-14)
    base_audit = {
        "method": (
            "lowdin_symmetric_orthogonalization_then_factorwise_"
            "empirical_bayes_icir"
        ),
        "eligible_factors": [
            config.factor_columns[index] for index in active
        ],
        "excluded_factors": excluded,
        "matured_event_count": len(matured),
        "required_history": int(config.factor_min_history),
        "lookback": int(config.factor_lookback),
        "prior_observations": float(
            config.factor_evidence_prior_observations
        ),
        "prior_scale": float(config.factor_ic_mean_prior_std),
        "evidence_clip": float(config.factor_evidence_clip),
        "future_data_used": False,
        "test_used_for_calibration_or_selection": False,
        "fallback_used": False,
    }
    if not len(active):
        return None, evidence, {
            **base_audit,
            "status": "warmup",
            "reason": "no_factor_has_sufficient_causal_evidence",
        }
    effective_cap = max(
        float(config.factor_absolute_weight_cap), 1.0 / len(active)
    )
    try:
        weights_array, cap_audit = _capped_signed_l1_weights(
            directions, effective_cap
        )
    except CSI500DataContractError as exc:
        return None, evidence, {
            **base_audit, "status": "warmup", "reason": str(exc)
        }
    weights = {
        factor: float(weight)
        for factor, weight in zip(config.factor_columns, weights_array)
        if abs(float(weight)) > 1.0e-14
    }
    for factor, weight in weights.items():
        evidence[factor]["final_weight"] = weight
    return weights, evidence, {
        **base_audit,
        "status": "ready",
        "raw_evidence": directions.tolist(),
        "final_weights": weights,
        "effective_absolute_weight_cap": effective_cap,
        **cap_audit,
    }


def _causal_walkforward_positive_ic_weights(
    matured: Sequence[Mapping[str, Any]],
    config: CSI500StrategyConfig,
) -> tuple[dict[str, float] | None, dict[str, dict[str, Any]], dict[str, Any]]:
    """Reproduce the validated v10 walk-forward positive-IC weighting rule."""

    raw_weights: dict[str, float] = {}
    evidence: dict[str, dict[str, Any]] = {}
    excluded: dict[str, str] = {}
    for factor in config.factor_columns:
        history = np.asarray([
            float(event["ic"][factor])
            for event in matured
            if event["ic"].get(factor) is not None
            and math.isfinite(float(event["ic"][factor]))
        ], dtype=float)[-int(config.factor_lookback):]
        if len(history) < int(config.factor_min_history):
            excluded[factor] = (
                f"history_below_minimum:{len(history)}<"
                f"{int(config.factor_min_history)}"
            )
            continue
        mean_ic = float(np.mean(history))
        standard_deviation = float(np.std(history, ddof=0))
        positive_ratio = float(np.mean(history > 0.0))
        stability = mean_ic / (standard_deviation + 1.0e-6)
        raw_weight = (
            mean_ic * positive_ratio
            * (1.0 + min(2.0, max(0.0, stability)))
            if mean_ic > 0.0
            and positive_ratio
            >= float(config.walkforward_positive_ic_min_hit_rate)
            else 0.0
        )
        evidence[factor] = {
            "observations": int(len(history)),
            "mean_rank_ic": mean_ic,
            "rank_ic_std": standard_deviation,
            "positive_ratio": positive_ratio,
            "stability": stability,
            "raw_weight": raw_weight,
        }
        if raw_weight > 0.0:
            raw_weights[factor] = raw_weight
        else:
            excluded[factor] = "nonpositive_ic_or_hit_rate_below_gate"

    audit = {
        "method": "walkforward_positive_ic_v10",
        "eligible_factors": sorted(raw_weights),
        "excluded_factors": excluded,
        "matured_event_count": len(matured),
        "required_history": int(config.factor_min_history),
        "lookback": int(config.factor_lookback),
        "minimum_positive_ic_ratio": float(
            config.walkforward_positive_ic_min_hit_rate
        ),
        "future_data_used": False,
        "test_used_for_calibration_or_selection": False,
        "fallback_used": False,
    }
    total = float(sum(raw_weights.values()))
    if total <= 0.0:
        return None, evidence, {
            **audit,
            "status": "warmup",
            "reason": "no_factor_passed_positive_ic_and_hit_rate_gate",
        }
    weights = {
        factor: float(value / total)
        for factor, value in raw_weights.items()
    }
    for factor, weight in weights.items():
        evidence[factor]["final_weight"] = weight
    return weights, evidence, {
        **audit,
        "status": "ready",
        "final_weights": weights,
        "weight_sum": float(sum(weights.values())),
    }


def build_causal_icir_scores(
    panel: pd.DataFrame,
    *,
    config: CSI500StrategyConfig | None = None,
) -> dict[str, Any]:
    """Build strictly causal monthly IC/ICIR scores with no score fallback."""

    config = config or CSI500StrategyConfig()
    _validate_config(config)
    frame = _normalize_panel(panel, config)
    groups = {
        str(date): group.sort_values("ts_code", kind="mergesort").reset_index(drop=True)
        for date, group in frame.groupby("signal_date", sort=True)
    }
    ic_events: list[dict[str, Any]] = []
    orthogonal_features: dict[str, np.ndarray] = {}
    contracts: dict[str, dict[str, Any]] = {}
    contract_errors: dict[str, str] = {}
    for signal_date, group in groups.items():
        try:
            contract = _period_contract(group, config)
            contracts[signal_date] = contract
        except CSI500DataContractError as exc:
            contract_errors[signal_date] = str(exc)
            continue
        event: dict[str, Any] = {
            "signal_date": signal_date,
            "maturity_date": contract["outcome_maturity_date"],
            "ic": {},
            "observations": {},
        }
        observed = pd.to_numeric(
            group.get(
                "alpha_view_observed", pd.Series(1, index=group.index)
            ),
            errors="coerce",
        ).fillna(0).astype(bool)
        ranked = np.column_stack([
            pd.to_numeric(group[factor], errors="coerce").rank(
                method="average", pct=True
            ).to_numpy(dtype=float)
            for factor in config.factor_columns
        ])
        orthogonal = symmetric_orthogonalize(
            ranked, observed.to_numpy(dtype=bool)
        )
        model_features = (
            ranked
            if config.factor_weight_method == "walkforward_positive_ic"
            else orthogonal
        )
        orthogonal_features[signal_date] = model_features
        for factor_index, factor in enumerate(config.factor_columns):
            ic, count = _rank_ic(
                pd.Series(model_features[:, factor_index], index=group.index),
                group["label_next_ret"],
                config.minimum_ic_cross_section,
                observed,
            )
            event["ic"][factor] = ic
            event["observations"][factor] = count
        ic_events.append(event)

    score_frames: list[pd.DataFrame] = []
    period_audit: list[dict[str, Any]] = []
    for signal_date in sorted(groups):
        group = groups[signal_date]
        if signal_date in contract_errors:
            period_audit.append({
                "signal_date": signal_date,
                "status": "blocked",
                "reason": contract_errors[signal_date],
                "weights": {},
            })
            continue
        matured = [
            event for event in ic_events
            if event["maturity_date"] < signal_date
        ]
        matured = sorted(
            matured, key=lambda item: (item["maturity_date"], item["signal_date"])
        )[-config.factor_lookback:]
        if config.factor_weight_method == "adaptive_bayesian_icir":
            weights, factor_evidence, ic_combination = (
                _causal_adaptive_icir_weights(matured, config)
            )
        elif config.factor_weight_method == "precision":
            weights, factor_evidence, ic_combination = (
                _causal_ic_precision_weights(matured, config)
            )
        elif config.factor_weight_method == "walkforward_positive_ic":
            weights, factor_evidence, ic_combination = (
                _causal_walkforward_positive_ic_weights(matured, config)
            )
        else:
            raise CSI500DataContractError(
                f"unsupported_factor_weight_method:"
                f"{config.factor_weight_method}"
            )
        if weights is None:
            period_audit.append({
                "signal_date": signal_date,
                "status": "warmup",
                "reason": ic_combination.get(
                    "reason", "causal_ic_precision_weights_unavailable"
                ),
                "matured_event_count": len(matured),
                "required_history": config.factor_min_history,
                "weights": {},
                "ic_combination": ic_combination,
                "fallback_used": False,
            })
            continue
        current_features = orthogonal_features[signal_date]
        raw_score = np.zeros(len(group), dtype=float)
        for factor_index, factor in enumerate(config.factor_columns):
            raw_score += float(weights.get(factor, 0.0)) * np.nan_to_num(
                current_features[:, factor_index], nan=0.0
            )
        if config.factor_weight_method == "walkforward_positive_ic":
            neutral_score = raw_score.copy()
            neutralization = {
                "status": "not_applied",
                "reason": (
                    "preserve_validated_walkforward_alpha; industry_and_style_"
                    "exposures_are_hard_constraints_in_the_weight_optimizer"
                ),
                "future_data_used": False,
            }
        else:
            try:
                neutral_score, neutralization = _neutralize_score(
                    group, raw_score, config
                )
            except CSI500DataContractError as exc:
                period_audit.append({
                    "signal_date": signal_date,
                    "status": "blocked",
                    "reason": str(exc),
                    "weights": weights,
                    "ic_combination": ic_combination,
                    "fallback_used": False,
                })
                continue
        maturity_cutoff = max(event["maturity_date"] for event in matured)
        factor_weights_json = json.dumps(
            _canonical(weights), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        neutralization_json = json.dumps(
            _canonical(neutralization), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        source_columns = [
            "signal_date", "maturity_date", "ts_code", "benchmark_weight",
            "industry", *config.factor_columns, *config.style_columns,
            "benchmark_weight_normalization_json",
        ]
        source_hash = _hash_parts(
            group[source_columns], matured, weights, ic_combination,
            neutralization,
            {
                "lookback": config.factor_lookback,
                "min_history": config.factor_min_history,
                "mean_prior_std": config.factor_ic_mean_prior_std,
                "covariance_shrinkage": config.factor_ic_covariance_shrinkage,
                "covariance_ridge": config.factor_ic_covariance_ridge,
                "absolute_weight_cap": config.factor_absolute_weight_cap,
            },
        )
        scored = group[[
            "signal_date", "maturity_date", "ts_code", "benchmark_weight",
            "industry", *config.style_columns,
            "benchmark_weight_normalization_json",
        ]].copy()
        scored = scored.rename(columns={"maturity_date": "outcome_maturity_date"})
        scored["maturity_date"] = maturity_cutoff
        scored["score_name"] = config.score_name
        scored["raw_score"] = raw_score
        scored["score"] = neutral_score
        scored["source_hash"] = source_hash
        scored["factor_weights_json"] = factor_weights_json
        scored["neutralization_json"] = neutralization_json
        score_frames.append(scored)
        period_audit.append({
            "signal_date": signal_date,
            "status": "ready",
            "maturity_date": maturity_cutoff,
            "outcome_maturity_date": contracts[signal_date]["outcome_maturity_date"],
            "matured_event_count": len(matured),
            "weights": weights,
            "factor_evidence": factor_evidence,
            "ic_combination": ic_combination,
            "neutralization": neutralization,
            "benchmark_weight_normalization":
                contracts[signal_date]["benchmark_weight_normalization"],
            "source_hash": source_hash,
            "fallback_used": False,
        })
    scores = pd.concat(score_frames, ignore_index=True) if score_frames else pd.DataFrame()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready" if not scores.empty else "blocked",
        "scores": scores,
        "periods": period_audit,
        "ic_events": ic_events,
        "fallback_used": False,
        "point_in_time_policy": "only_label_maturity_strictly_before_signal_date",
    }


def _database_path_from_connection(
    connection: sqlite3.Connection,
) -> str | None:
    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        try:
            name = str(row[1])
            path = str(row[2] or "")
        except Exception:
            continue
        if name == "main" and path:
            return path
    return None


def _safe_numeric_series(
    frame: pd.DataFrame,
    column: str,
    default: float = np.nan,
) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(default, index=frame.index, dtype=float)


def _safe_divide_series(
    numerator: pd.Series,
    denominator: pd.Series,
) -> pd.Series:
    denominator = pd.to_numeric(denominator, errors="coerce").replace(0.0, np.nan)
    return pd.to_numeric(numerator, errors="coerce") / denominator


def _attach_factor_lab_bridge_daily_features(
    connection: sqlite3.Connection,
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach PIT daily rolling features required by the Factor Lab bridge."""

    if frame.empty:
        return frame.copy(), {"status": "empty_panel"}
    signals = sorted(frame["signal_date"].map(_date_text).unique().tolist())
    codes = sorted(frame["ts_code"].astype(str).unique().tolist())
    if not signals or not codes:
        return frame.copy(), {"status": "empty_key"}
    min_signal, max_signal = signals[0], signals[-1]
    lookback_start = (
        pd.to_datetime(min_signal, format="%Y%m%d") - pd.Timedelta(days=460)
    ).strftime("%Y%m%d")
    try:
        rows = connection.execute(
            """
            select trade_date from trade_calendar
            where is_trade_day=1 and trade_date<=?
            order by trade_date desc limit 320
            """,
            (min_signal,),
        ).fetchall()
        if rows:
            lookback_start = str(rows[-1][0])
    except sqlite3.Error:
        pass

    daily_frames: list[pd.DataFrame] = []
    chunk_size = 700
    for offset in range(0, len(codes), chunk_size):
        chunk = codes[offset: offset + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        query = f"""
            select trade_date, ts_code, qfq_close, pre_close, high, low, vol, amount
            from stock_ohlcv_daily
            where trade_date between ? and ? and ts_code in ({placeholders})
            order by ts_code, trade_date
        """
        daily_frames.append(
            pd.read_sql_query(
                query,
                connection,
                params=[lookback_start, max_signal, *chunk],
            )
        )
    daily = (
        pd.concat(daily_frames, ignore_index=True)
        if daily_frames else pd.DataFrame()
    )
    if daily.empty:
        raise CSI500DataContractError(
            "factor_lab_bridge_daily_ohlcv_unavailable"
        )
    daily["trade_date"] = daily["trade_date"].map(_date_text)
    daily["ts_code"] = daily["ts_code"].astype(str)
    for column in ("qfq_close", "pre_close", "high", "low", "vol", "amount"):
        daily[column] = pd.to_numeric(daily[column], errors="coerce")
    daily = daily.sort_values(["ts_code", "trade_date"], kind="mergesort")
    grouped = daily.groupby("ts_code", sort=False)
    close = daily["qfq_close"].where(daily["qfq_close"] > 0.0)
    ret = grouped["qfq_close"].pct_change(fill_method=None)
    daily["factor_lab_vol_20"] = (
        ret.groupby(daily["ts_code"]).rolling(20, min_periods=12).std()
        .reset_index(level=0, drop=True)
    )
    negative = ret.clip(upper=0.0)
    daily["factor_lab_down_vol_20"] = (
        negative.groupby(daily["ts_code"]).rolling(20, min_periods=12).std()
        .reset_index(level=0, drop=True)
    )
    low60 = (
        grouped["qfq_close"].rolling(60, min_periods=30).min()
        .reset_index(level=0, drop=True)
    )
    high60 = (
        grouped["qfq_close"].rolling(60, min_periods=30).max()
        .reset_index(level=0, drop=True)
    )
    daily["factor_lab_price_pos_60"] = (
        (close - low60) / (high60 - low60).replace(0.0, np.nan) - 0.5
    )
    log_vol = np.log1p(daily["vol"].clip(lower=0.0))
    vol_mean = (
        log_vol.groupby(daily["ts_code"]).rolling(20, min_periods=12).mean()
        .reset_index(level=0, drop=True)
    )
    vol_std = (
        log_vol.groupby(daily["ts_code"]).rolling(20, min_periods=12).std()
        .reset_index(level=0, drop=True)
    )
    daily["factor_lab_volume_z_20"] = (
        (log_vol - vol_mean) / vol_std.replace(0.0, np.nan)
    )
    illiq = ret.abs() / daily["amount"].abs().replace(0.0, np.nan)
    daily["factor_lab_amihud_20"] = np.log1p(
        illiq.groupby(daily["ts_code"]).rolling(20, min_periods=12).mean()
        .reset_index(level=0, drop=True) * 1.0e8
    )
    daily_features = daily.rename(columns={"trade_date": "signal_date"})[[
        "signal_date", "ts_code", "factor_lab_vol_20",
        "factor_lab_down_vol_20", "factor_lab_price_pos_60",
        "factor_lab_volume_z_20", "factor_lab_amihud_20",
    ]]
    enriched = frame.merge(
        daily_features,
        how="left",
        on=["signal_date", "ts_code"],
        validate="one_to_one",
    )
    coverage = {
        column: float(pd.to_numeric(enriched[column], errors="coerce").notna().mean())
        for column in daily_features.columns
        if column not in {"signal_date", "ts_code"}
    }
    return enriched, {
        "status": "ready",
        "source_table": "stock_ohlcv_daily",
        "lookback_start": lookback_start,
        "min_signal_date": min_signal,
        "max_signal_date": max_signal,
        "asset_count": len(codes),
        "daily_rows": int(len(daily)),
        "coverage": coverage,
        "point_in_time_policy": "daily rolling features use rows trade_date<=signal_date",
    }


def _factor_lab_bridge_base_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    out = pd.DataFrame({
        "trade_date": work["signal_date"].map(_date_text),
        "signal_date": work["signal_date"].map(_date_text),
        "maturity_date": work["maturity_date"].map(_date_text),
        "ts_code": work["ts_code"].astype(str),
        "factor_lab_bridge_target": pd.to_numeric(
            work["label_next_ret"], errors="coerce"
        ),
        "alpha_view_observed": pd.to_numeric(
            work.get("alpha_view_observed", pd.Series(1, index=work.index)),
            errors="coerce",
        ).fillna(0.0).astype(bool),
    }, index=work.index)
    px = _safe_numeric_series(work, "px")
    out["ret_1"] = _safe_numeric_series(work, "ret1")
    if out["ret_1"].isna().all():
        out["ret_1"] = _safe_divide_series(px, _safe_numeric_series(work, "px1")) - 1.0
    out["ret_5"] = _safe_numeric_series(work, "mom5")
    out["ret_20"] = _safe_numeric_series(work, "mom20")
    out["ret_60"] = _safe_numeric_series(work, "mom60")
    out["vol_20"] = _safe_numeric_series(work, "factor_lab_vol_20")
    out["down_vol_20"] = _safe_numeric_series(work, "factor_lab_down_vol_20")
    out["price_pos_60"] = _safe_numeric_series(work, "factor_lab_price_pos_60")
    out["volume_z_20"] = _safe_numeric_series(work, "factor_lab_volume_z_20")
    out["amihud_20"] = _safe_numeric_series(work, "factor_lab_amihud_20")
    out["turnover"] = _safe_numeric_series(work, "turnover_rate") / 100.0
    out["volume_ratio"] = _safe_numeric_series(work, "volume_ratio")
    out["value_ep"] = np.where(
        _safe_numeric_series(work, "pe_ttm") > 0.0,
        1.0 / _safe_numeric_series(work, "pe_ttm"),
        np.nan,
    )
    out["value_bp"] = np.where(
        _safe_numeric_series(work, "pb") > 0.0,
        1.0 / _safe_numeric_series(work, "pb"),
        np.nan,
    )
    out["value_sp"] = np.where(
        _safe_numeric_series(work, "ps_ttm") > 0.0,
        1.0 / _safe_numeric_series(work, "ps_ttm"),
        np.nan,
    )
    out["dividend"] = _safe_numeric_series(work, "dv_ttm") / 100.0
    out["log_mv"] = np.log(
        _safe_numeric_series(work, "circ_mv").where(
            _safe_numeric_series(work, "circ_mv") > 0.0
        )
    )
    amount = _safe_numeric_series(work, "amount").abs().replace(0.0, np.nan)
    out["moneyflow"] = _safe_divide_series(
        _safe_numeric_series(work, "net_mf_amount"), amount
    )
    out["large_flow"] = _safe_divide_series(
        _safe_numeric_series(work, "buy_lg_amount")
        - _safe_numeric_series(work, "sell_lg_amount"),
        amount,
    )
    out["extreme_flow"] = _safe_divide_series(
        _safe_numeric_series(work, "buy_elg_amount")
        - _safe_numeric_series(work, "sell_elg_amount"),
        amount,
    )
    out["range_1"] = _safe_numeric_series(work, "range_pct")
    if out["range_1"].isna().all():
        out["range_1"] = _safe_divide_series(
            _safe_numeric_series(work, "high") - _safe_numeric_series(work, "low"),
            _safe_numeric_series(work, "pre_close"),
        )
    out["gap_1"] = _safe_numeric_series(work, "gap_pct")
    out["quality_roe"] = _safe_numeric_series(work, "roe")
    out["quality_roa"] = _safe_numeric_series(work, "roa")
    out["quality_gross_margin"] = _safe_numeric_series(work, "gross_margin")
    out["quality_asset_turn"] = _safe_numeric_series(work, "assets_turn")
    out["quality_low_leverage"] = -_safe_numeric_series(work, "debt_to_assets")
    out["growth_revenue"] = _safe_numeric_series(work, "tr_yoy")
    out["growth_operating_profit"] = _safe_numeric_series(work, "op_yoy")
    out["growth_net_profit"] = _safe_numeric_series(work, "netprofit_yoy")
    for column in FACTOR_LAB_BRIDGE_FEATURES:
        out[column] = pd.to_numeric(out[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
    return out


def _factor_lab_bridge_split(
    dates: Sequence[str],
    config: CSI500StrategyConfig,
) -> dict[str, tuple[int, int]]:
    ordered = [_date_text(value) for value in dates]
    train_end = _date_text(config.train_end)
    validation_end = _date_text(config.validation_end)
    train_count = sum(date <= train_end for date in ordered)
    validation_count = sum(date <= validation_end for date in ordered)
    return {
        "train": (0, train_count),
        "valid": (train_count, validation_count),
        "test": (validation_count, len(ordered)),
    }


def _rank_center(values: pd.Series) -> pd.Series:
    ranked = pd.to_numeric(values, errors="coerce").rank(
        method="average", pct=True
    )
    return ranked - 0.5


def build_factor_lab_champion_scores_from_database(
    connection: sqlite3.Connection,
    panel: pd.DataFrame,
    *,
    config: CSI500StrategyConfig | None = None,
) -> dict[str, Any]:
    """Build CSI500 monthly scores from the governed Factor Lab profile.

    The scorer is still a point-in-time CSI500 scorer: every refit for signal
    ``t`` uses only rows whose outcome maturity date is strictly before ``t``.
    It maps the Factor Lab feature family onto the exact index member panel and
    then emits one score for every member required by the stock optimizer.
    """

    config = config or CSI500StrategyConfig()
    _validate_config(config)
    frame = _normalize_panel(panel, config)
    frame, daily_audit = _attach_factor_lab_bridge_daily_features(
        connection, frame
    )
    feature_frame = _factor_lab_bridge_base_frame(frame)
    dates = sorted(feature_frame["signal_date"].unique().tolist())
    assets = sorted(feature_frame["ts_code"].unique().tolist())
    split = _factor_lab_bridge_split(dates, config)
    feature_names = list(FACTOR_LAB_BRIDGE_FEATURES)
    warehouse_audit: dict[str, Any] = {
        "enabled": False,
        "feature_count": len(feature_names),
        "selected_count": 0,
    }
    if bool(config.factor_lab_bridge_include_warehouse_factors):
        database_path = _database_path_from_connection(connection)
        if not database_path:
            raise CSI500DataContractError(
                "factor_lab_bridge_database_path_unavailable"
            )
        try:
            from model.factor_laboratory.unified_factor_panel import (
                extend_with_screened_factors,
            )
            feature_frame, feature_names, warehouse_audit = (
                extend_with_screened_factors(
                    feature_frame,
                    database_path=database_path,
                    base_features=list(FACTOR_LAB_BRIDGE_FEATURES),
                    target_col="factor_lab_bridge_target",
                    date_order=dates,
                    assets=assets,
                    split=split,
                    config={
                        "factor_universe_mode": "screened_full",
                        "use_unified_factor_panel": True,
                        "max_factor_candidates": int(
                            config.factor_lab_bridge_max_factor_candidates
                        ),
                        "factor_screen_top_n": int(
                            config.factor_lab_bridge_screen_top_n
                        ),
                        "factor_screen_lookback_days": int(
                            config.factor_lab_bridge_screen_lookback_days
                        ),
                        "factor_screen_rebalance_days": int(
                            config.factor_lab_bridge_screen_rebalance_days
                        ),
                        "factor_screen_min_coverage": float(
                            config.factor_lab_bridge_screen_min_coverage
                        ),
                        "factor_screen_min_dates": int(
                            config.factor_lab_bridge_screen_min_dates
                        ),
                        "factor_screen_min_assets_per_date": int(
                            config.factor_lab_bridge_screen_min_assets_per_date
                        ),
                        "factor_screen_max_pair_corr": float(
                            config.factor_lab_bridge_screen_max_pair_corr
                        ),
                        "external_factor_max_staleness_days": int(
                            config.factor_lab_bridge_external_factor_max_staleness_days
                        ),
                        "include_subject_parquet": False,
                    },
                )
            )
        except Exception as exc:
            raise CSI500DataContractError(
                "factor_lab_bridge_warehouse_extension_failed:"
                f"{type(exc).__name__}:{exc}"
            ) from exc
    if len(feature_names) < len(FACTOR_LAB_LEGACY_FEATURES):
        raise CSI500DataContractError(
            "factor_lab_bridge_feature_count_below_legacy_requirement"
        )

    date_index = {date: idx for idx, date in enumerate(dates)}
    asset_index = {code: idx for idx, code in enumerate(assets)}
    feature_array = np.full(
        (len(dates), len(assets), len(feature_names)), np.nan, dtype=float
    )
    target_array = np.full((len(dates), len(assets)), np.nan, dtype=float)
    member_array = np.zeros((len(dates), len(assets)), dtype=bool)
    row_di = feature_frame["signal_date"].map(date_index).to_numpy(dtype=int)
    row_ai = feature_frame["ts_code"].map(asset_index).to_numpy(dtype=int)
    feature_array[row_di, row_ai] = feature_frame[feature_names].to_numpy(
        dtype=float
    )
    target_array[row_di, row_ai] = pd.to_numeric(
        feature_frame["factor_lab_bridge_target"], errors="coerce"
    ).to_numpy(dtype=float)
    member_array[row_di, row_ai] = feature_frame["alpha_view_observed"].to_numpy(
        dtype=bool
    )
    min_feature_count = max(
        8, int(math.ceil(min(len(feature_names), len(FACTOR_LAB_LEGACY_FEATURES)) * 0.60))
    )
    enough_features = np.isfinite(feature_array).sum(axis=2) >= min_feature_count
    valid_for_training = member_array & enough_features & np.isfinite(target_array)

    ranked_features = np.zeros_like(feature_array, dtype=float)
    for idx in range(len(dates)):
        ranked_features[idx] = (
            pd.DataFrame(feature_array[idx]).rank(axis=0, pct=True)
            .sub(0.5).fillna(0.0).to_numpy(dtype=float)
        )
    try:
        from model.factor_laboratory.adaptive_icir import (
            causal_rolling_icir_scores,
        )
    except ImportError:  # pragma: no cover - direct script compatibility.
        from adaptive_icir import causal_rolling_icir_scores
    adaptive_scores, adaptive_audit = causal_rolling_icir_scores(
        ranked_features,
        target_array,
        valid_for_training,
        1,
        lookback_periods=int(config.factor_lab_bridge_lookback_periods),
        min_periods=int(config.factor_lab_bridge_min_periods),
    )

    from sklearn.linear_model import LinearRegression

    scores_by_signal: dict[str, np.ndarray] = {}
    score_frames: list[pd.DataFrame] = []
    period_audit: list[dict[str, Any]] = []
    legacy_indexes = [feature_names.index(name) for name in FACTOR_LAB_LEGACY_FEATURES]
    profile = str(config.factor_lab_profile)
    selected_model = (
        "incumbent_ols"
        if profile == "strict_turnover_065"
        else "incumbent_ols_adaptive_icir_rank_ensemble"
    )
    selected_execution_policy = "robust_volatility_budget_rank_buffer"
    for signal_date, group in frame.groupby("signal_date", sort=True):
        signal = _date_text(signal_date)
        group = group.sort_values("ts_code", kind="mergesort").reset_index(drop=True)
        current_codes = group["ts_code"].astype(str).tolist()
        current_feature_rows = feature_frame[
            feature_frame["signal_date"] == signal
        ].set_index("ts_code").reindex(current_codes)
        history = feature_frame[
            (feature_frame["maturity_date"] < signal)
            & pd.to_numeric(
                feature_frame["factor_lab_bridge_target"], errors="coerce"
            ).notna()
            & feature_frame["alpha_view_observed"].astype(bool)
        ].copy()
        history_feature_count = np.isfinite(
            history[feature_names].to_numpy(dtype=float)
        ).sum(axis=1) if not history.empty else np.array([], dtype=int)
        if not history.empty:
            history = history.loc[
                history_feature_count >= min_feature_count
            ].copy()
        unique_history_dates = int(history["signal_date"].nunique()) if not history.empty else 0
        if (
            unique_history_dates < int(config.factor_lab_bridge_min_training_periods)
            or len(history) < int(config.factor_lab_bridge_min_training_rows)
        ):
            period_audit.append({
                "signal_date": signal,
                "status": "warmup",
                "reason": "factor_lab_bridge_training_history_insufficient",
                "history_periods": unique_history_dates,
                "history_rows": int(len(history)),
                "required_history_periods": int(config.factor_lab_bridge_min_training_periods),
                "required_history_rows": int(config.factor_lab_bridge_min_training_rows),
                "fallback_used": False,
            })
            continue
        train_x_raw = history[feature_names].to_numpy(dtype=float)[:, legacy_indexes]
        train_y = pd.to_numeric(
            history["factor_lab_bridge_target"], errors="coerce"
        ).to_numpy(dtype=float)
        current_x_raw = current_feature_rows[feature_names].to_numpy(dtype=float)[:, legacy_indexes]
        current_feature_count = np.isfinite(
            current_feature_rows[feature_names].to_numpy(dtype=float)
        ).sum(axis=1)
        current_valid = (
            current_feature_rows["alpha_view_observed"].fillna(False).to_numpy(dtype=bool)
            & (current_feature_count >= min_feature_count)
        )
        median = np.nanmedian(train_x_raw, axis=0)
        q25 = np.nanpercentile(train_x_raw, 25, axis=0)
        q75 = np.nanpercentile(train_x_raw, 75, axis=0)
        scale = np.where(q75 - q25 > 1.0e-6, q75 - q25, 1.0)
        train_x = np.nan_to_num(
            (train_x_raw - median) / scale,
            nan=0.0, posinf=8.0, neginf=-8.0,
        )
        train_x = np.clip(train_x, -8.0, 8.0)
        current_x = np.nan_to_num(
            (current_x_raw - median) / scale,
            nan=0.0, posinf=8.0, neginf=-8.0,
        )
        current_x = np.clip(current_x, -8.0, 8.0)
        model = LinearRegression()
        model.fit(train_x, train_y)
        ols_raw = np.full(len(group), np.nan, dtype=float)
        ols_raw[current_valid] = model.predict(current_x[current_valid])
        if profile == "strict_turnover_065":
            raw_score = ols_raw
            component_audit = {
                "profile": profile,
                "selected_model": selected_model,
                "components": {"incumbent_ols": 1.0},
            }
        else:
            date_pos = date_index[signal]
            adaptive_raw = np.array([
                adaptive_scores[date_pos, asset_index[code]]
                for code in current_codes
            ], dtype=float)
            both_valid = current_valid & np.isfinite(ols_raw) & np.isfinite(adaptive_raw)
            if int(both_valid.sum()) < int(config.expected_members * config.precomputed_score_min_coverage):
                period_audit.append({
                    "signal_date": signal,
                    "status": "warmup",
                    "reason": "factor_lab_bridge_hybrid_component_coverage_insufficient",
                    "available_members": int(both_valid.sum()),
                    "required_members": int(config.expected_members * config.precomputed_score_min_coverage),
                    "fallback_used": False,
                })
                continue
            raw_score = np.full(len(group), np.nan, dtype=float)
            ols_rank = _rank_center(pd.Series(ols_raw[both_valid]))
            adaptive_rank = _rank_center(pd.Series(adaptive_raw[both_valid]))
            raw_score[both_valid] = 0.5 * ols_rank.to_numpy(dtype=float) + 0.5 * adaptive_rank.to_numpy(dtype=float)
            component_audit = {
                "profile": profile,
                "selected_model": selected_model,
                "components": {
                    "incumbent_ols": 0.5,
                    "adaptive_icir_12m_neutral": 0.5,
                },
                "adaptive_icir": adaptive_audit,
            }
        score_valid = np.isfinite(raw_score)
        required = int(config.expected_members * config.precomputed_score_min_coverage)
        if int(score_valid.sum()) < required:
            period_audit.append({
                "signal_date": signal,
                "status": "blocked",
                "reason": "factor_lab_bridge_score_coverage_below_threshold",
                "available_members": int(score_valid.sum()),
                "required_members": required,
                "fallback_used": False,
            })
            continue
        if not np.isfinite(raw_score).all():
            filled = raw_score.copy()
            filled[~np.isfinite(filled)] = 0.0
            raw_score = filled
        if bool(config.factor_lab_bridge_alpha_neutralization):
            neutral_score, neutralization = _neutralize_score(
                group, raw_score, config
            )
        else:
            neutral_score = raw_score.copy()
            neutralization = {
                "status": "not_applied",
                "reason": "factor_lab_bridge_alpha_neutralization_disabled",
                "future_data_used": False,
            }
        history_maturity = sorted(history["maturity_date"].astype(str).unique())[-1]
        factor_payload = {
            "model": selected_model,
            "execution_policy": selected_execution_policy,
            "profile": profile,
            "legacy_features": list(FACTOR_LAB_LEGACY_FEATURES),
            "feature_count": len(feature_names),
            "selected_external_features": warehouse_audit.get(
                "selected_external_features", []
            ),
            "ols_coefficients": {
                name: _finite(value, name)
                for name, value in zip(FACTOR_LAB_LEGACY_FEATURES, model.coef_)
            },
            "ols_intercept": _finite(float(model.intercept_), "ols_intercept"),
            "train_history_rows": int(len(history)),
            "train_history_periods": unique_history_dates,
            "latest_training_maturity_date": history_maturity,
            "test_usage": "never_used_for_model_refit_or_parameter_selection",
        }
        source_columns = [
            "signal_date", "maturity_date", "ts_code", "benchmark_weight",
            "industry", *config.style_columns,
            "benchmark_weight_normalization_json",
        ]
        source_hash = _hash_parts(
            group[source_columns],
            feature_names,
            factor_payload,
            neutralization,
            {
                "score_source_mode": "factor_lab_champion",
                "factor_lab_profile": profile,
                "warehouse_audit": warehouse_audit,
                "daily_feature_audit": daily_audit,
            },
        )
        scored = group[[
            "signal_date", "maturity_date", "ts_code", "benchmark_weight",
            "industry", *config.style_columns,
            "benchmark_weight_normalization_json",
        ]].copy()
        scored = scored.rename(columns={"maturity_date": "outcome_maturity_date"})
        scored["maturity_date"] = history_maturity
        scored["score_name"] = "factor_lab_champion_" + profile
        scored["raw_score"] = raw_score
        scored["score"] = neutral_score
        scored["source_hash"] = source_hash
        scored["factor_weights_json"] = json.dumps(
            _canonical(factor_payload), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        scored["neutralization_json"] = json.dumps(
            _canonical(neutralization), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
        score_frames.append(scored)
        scores_by_signal[signal] = neutral_score
        period_audit.append({
            "signal_date": signal,
            "status": "ready",
            "maturity_date": history_maturity,
            "outcome_maturity_date": str(group["maturity_date"].iloc[0]),
            "history_periods": unique_history_dates,
            "history_rows": int(len(history)),
            "profile": profile,
            "selected_model": selected_model,
            "selected_execution_policy": selected_execution_policy,
            "component_audit": component_audit,
            "neutralization": neutralization,
            "benchmark_weight_normalization": json.loads(
                str(group["benchmark_weight_normalization_json"].iloc[0])
            ),
            "source_hash": source_hash,
            "fallback_used": False,
        })
    scores = pd.concat(score_frames, ignore_index=True) if score_frames else pd.DataFrame()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready" if not scores.empty else "blocked",
        "scores": scores,
        "periods": period_audit,
        "fallback_used": False,
        "score_source": {
            "mode": "factor_lab_champion",
            "profile": profile,
            "selected_model": selected_model,
            "selected_execution_policy": selected_execution_policy,
            "feature_count": len(feature_names),
            "base_feature_count": len(FACTOR_LAB_BRIDGE_FEATURES),
            "warehouse": warehouse_audit,
            "daily_features": daily_audit,
            "score_name": "factor_lab_champion_" + profile,
        },
        "point_in_time_policy": (
            "rolling_refit_uses_only_rows_with_outcome_maturity_date_strictly_"
            "before_current_signal_date"
        ),
    }


def load_precomputed_optimizer_scores(
    connection: sqlite3.Connection,
    panel: pd.DataFrame,
    *,
    config: CSI500StrategyConfig | None = None,
) -> dict[str, Any]:
    """Load fixed-frequency PIT stock scores from the audited optimizer table."""

    config = config or CSI500StrategyConfig()
    _validate_config(config)
    if str(config.score_source_mode) != "precomputed_database":
        raise CSI500DataContractError(
            "precomputed_score_loader_requires_precomputed_database_mode"
        )
    frame = _normalize_panel(panel, config)
    score_name = str(config.precomputed_score_name or config.score_name).strip()
    if not score_name:
        raise CSI500DataContractError("precomputed_score_name_required")
    dates = sorted(frame["signal_date"].astype(str).unique().tolist())
    if not dates:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "scores": pd.DataFrame(),
            "periods": [],
            "ic_events": [],
            "fallback_used": False,
            "point_in_time_policy": "precomputed_score_table_no_panel_dates",
        }
    placeholders = ",".join("?" for _ in dates)
    clauses = ["score_name=?", f"signal_date in ({placeholders})"]
    params: list[Any] = [score_name, *dates]
    run_id = (
        str(config.precomputed_score_run_id).strip()
        if config.precomputed_score_run_id is not None else ""
    )
    if run_id:
        clauses.insert(0, "score_run_id=?")
        params.insert(0, run_id)
    raw = pd.read_sql_query(
        f"""
        select score_run_id, signal_date, maturity_date, ts_code, score_name,
               score, raw_score, factor_weights_json, neutralization_json,
               source_hash
          from {SCORE_TABLE}
         where {' and '.join(clauses)}
        """,
        connection,
        params=params,
    )
    if raw.empty:
        periods = [
            {
                "signal_date": date,
                "status": "blocked",
                "reason": "precomputed_score_rows_not_found",
                "score_name": score_name,
                "score_run_id": run_id or None,
                "fallback_used": False,
            }
            for date in dates
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "scores": pd.DataFrame(),
            "periods": periods,
            "ic_events": [],
            "fallback_used": False,
            "point_in_time_policy": "audited_precomputed_optimizer_factor_score_period",
        }
    raw["signal_date"] = raw["signal_date"].map(_date_text)
    raw["ts_code"] = raw["ts_code"].astype(str)
    raw["score"] = pd.to_numeric(raw["score"], errors="coerce")
    raw["raw_score"] = pd.to_numeric(
        raw.get("raw_score", raw["score"]), errors="coerce"
    ).fillna(raw["score"])
    if raw[["score", "raw_score"]].isna().any().any():
        raise CSI500DataContractError(
            "precomputed_score_contains_missing_or_non_numeric_values"
        )
    duplicate_key = raw.duplicated(["signal_date", "ts_code"], keep=False)
    if duplicate_key.any():
        sample = raw.loc[duplicate_key, ["signal_date", "ts_code"]].head(5)
        raise CSI500DataContractError(
            "precomputed_score_duplicate_rows:" + sample.to_json(orient="records")
        )

    base_columns = [
        "signal_date", "maturity_date", "ts_code", "benchmark_weight",
        "industry", *config.style_columns, "benchmark_weight_normalization_json",
    ]
    missing_base = sorted(set(base_columns) - set(frame.columns))
    if missing_base:
        raise CSI500DataContractError(
            "precomputed_score_panel_missing_columns:" + ",".join(missing_base)
        )
    base = frame[base_columns].copy()
    base["alpha_view_observed"] = pd.to_numeric(
        frame.get("alpha_view_observed", pd.Series(1, index=frame.index)),
        errors="coerce",
    ).fillna(0).astype(bool)
    merged = base.merge(
        raw[[
            "signal_date", "ts_code", "score_run_id", "score_name", "score",
            "raw_score", "maturity_date", "factor_weights_json",
            "neutralization_json", "source_hash",
        ]].rename(columns={"maturity_date": "score_maturity_date"}),
        on=["signal_date", "ts_code"],
        how="left",
        validate="one_to_one",
    )
    periods: list[dict[str, Any]] = []
    ready_dates: set[str] = set()
    for signal_date, group in merged.groupby("signal_date", sort=True):
        observed = group["alpha_view_observed"].astype(bool)
        required_count = int(observed.sum())
        available_required = int(group.loc[observed, "score"].notna().sum())
        coverage = (
            available_required / required_count if required_count else 0.0
        )
        missing_required = required_count - available_required
        row = {
            "signal_date": str(signal_date),
            "status": "ready" if missing_required == 0 else "blocked",
            "reason": None if missing_required == 0 else "precomputed_score_missing_required_members",
            "score_name": score_name,
            "score_run_id": run_id or None,
            "required_members": required_count,
            "available_required_members": available_required,
            "coverage": float(coverage),
            "minimum_coverage": float(config.precomputed_score_min_coverage),
            "fallback_used": False,
            "score_source": "precomputed_database",
        }
        if coverage < float(config.precomputed_score_min_coverage):
            row["status"] = "blocked"
            row["reason"] = "precomputed_score_coverage_below_minimum"
        if row["status"] == "ready":
            ready_dates.add(str(signal_date))
        periods.append(row)
    scored = merged[merged["signal_date"].astype(str).isin(ready_dates)].copy()
    if not scored.empty:
        scored.loc[~scored["alpha_view_observed"], "score"] = scored.loc[
            ~scored["alpha_view_observed"], "score"
        ].fillna(0.0)
        scored.loc[~scored["alpha_view_observed"], "raw_score"] = scored.loc[
            ~scored["alpha_view_observed"], "raw_score"
        ].fillna(0.0)
    score_frame = scored[[
        "signal_date", "maturity_date", "ts_code", "benchmark_weight",
        "industry", *config.style_columns, "benchmark_weight_normalization_json",
        "score", "raw_score", "score_name", "score_run_id",
        "score_maturity_date", "factor_weights_json", "neutralization_json",
        "source_hash",
    ]].copy() if not scored.empty else pd.DataFrame()
    if not score_frame.empty:
        score_frame["maturity_date"] = score_frame["score_maturity_date"].fillna(
            score_frame["maturity_date"]
        )
        score_frame["score_name"] = config.score_name
        score_frame["factor_weights_json"] = score_frame["factor_weights_json"].fillna("{}")
        score_frame["neutralization_json"] = score_frame[
            "neutralization_json"
        ].fillna("{}")
        score_frame["source_hash"] = score_frame["source_hash"].fillna(
            _hash_parts("precomputed_score", score_name, run_id)
        )
        score_frame = score_frame.drop(
            columns=["score_run_id", "score_maturity_date"], errors="ignore"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready" if ready_dates else "blocked",
        "scores": score_frame,
        "periods": periods,
        "ic_events": [],
        "fallback_used": False,
        "point_in_time_policy": "audited_precomputed_optimizer_factor_score_period",
        "score_source": {
            "table": SCORE_TABLE,
            "score_name": score_name,
            "score_run_id": run_id or None,
            "ready_periods": len(ready_dates),
            "requested_periods": len(dates),
        },
    }


def _matured_return_matrix(
    panel: pd.DataFrame,
    signal_date: str,
    codes: Sequence[str],
    config: CSI500StrategyConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    history = panel[panel["maturity_date"] < signal_date].copy()
    history = history[np.isfinite(history["label_next_ret"])]
    if history.empty:
        raise CSI500DataContractError("risk_history_empty")
    matrix = history.pivot_table(
        index="maturity_date", columns="ts_code", values="label_next_ret", aggfunc="last"
    ).sort_index().tail(config.risk_lookback)
    matrix = matrix.reindex(columns=list(codes))
    if len(matrix) < config.risk_min_observations:
        raise CSI500DataContractError(
            f"risk_history_observations_below_{config.risk_min_observations}:{len(matrix)}"
        )
    coverage = matrix.notna().mean()
    minimum_coverage = float(coverage.min()) if len(coverage) else 0.0
    if minimum_coverage < config.risk_min_asset_coverage:
        raise CSI500DataContractError(
            f"risk_asset_coverage_below_threshold:{minimum_coverage:.6f}"
        )
    if matrix.isna().all().any() or matrix.isna().all(axis=1).any():
        raise CSI500DataContractError("risk_history_contains_empty_asset_or_period")
    return matrix, {
        "observations": int(len(matrix)),
        "minimum_asset_coverage": minimum_coverage,
        "latest_maturity_date": str(matrix.index.max()),
        "strictly_before_signal": str(matrix.index.max()) < signal_date,
    }


def _resolve_risk(
    signal_date: str,
    group: pd.DataFrame,
    panel: pd.DataFrame,
    config: CSI500StrategyConfig,
    provider: RiskProvider | None,
) -> tuple[np.ndarray | None, np.ndarray | None, dict[str, Any]]:
    codes = group["ts_code"].astype(str).tolist()
    try:
        matured, history_audit = _matured_return_matrix(
            panel, signal_date, codes, config
        )
    except CSI500DataContractError:
        if provider is None:
            raise
        matured = pd.DataFrame(columns=codes, dtype=float)
        history_audit = {
            "observations": 0,
            "provider_supplied_without_internal_history": True,
        }
    styles = group[["ts_code", *config.style_columns]].copy()
    if provider is None:
        risk = build_psd_factor_risk_root(
            matured,
            group[["ts_code", "industry"]],
            style_exposures=styles,
            signal_date=signal_date,
            annualization=float(config.periods_per_year),
            half_life=min(24.0, float(config.risk_lookback)),
        )
        return risk["risk_root"], None, {
            **history_audit, **risk["diagnostics"], "provider": "internal_factor_risk",
        }
    supplied = provider(signal_date, group.copy(), matured.copy())
    diagnostics: dict[str, Any] = {**history_audit, "provider": "external_callable"}
    risk_root: Any = None
    covariance: Any = None
    supplied_codes: list[str] | None = None
    if isinstance(supplied, Mapping):
        risk_root = supplied.get("risk_root")
        covariance = supplied.get("annual_covariance")
        raw_codes = supplied.get("risk_asset_codes")
        if raw_codes is not None:
            supplied_codes = [str(code) for code in raw_codes]
        diagnostics.update(dict(supplied.get("diagnostics", {})))
    else:
        risk_root = supplied
    if (risk_root is None) == (covariance is None):
        raise CSI500DataContractError(
            "risk_provider_must_return_exactly_one_of_risk_root_or_annual_covariance"
        )
    if supplied_codes is not None:
        if len(supplied_codes) != len(set(supplied_codes)):
            raise CSI500DataContractError("risk_provider_duplicate_asset_codes")
        if set(supplied_codes) != set(codes):
            missing = sorted(set(codes) - set(supplied_codes))
            extra = sorted(set(supplied_codes) - set(codes))
            raise CSI500DataContractError(
                "risk_provider_asset_codes_mismatch:"
                f"missing={','.join(missing[:12])};extra={','.join(extra[:12])}"
            )
    if risk_root is not None:
        if isinstance(risk_root, pd.DataFrame):
            labelled = risk_root.copy()
            labelled.columns = labelled.columns.astype(str)
            if set(labelled.columns) != set(codes) or labelled.columns.duplicated().any():
                raise CSI500DataContractError(
                    "risk_provider_labelled_root_columns_mismatch"
                )
            array = labelled.reindex(columns=codes).to_numpy(dtype=float)
            diagnostics["asset_order_validation"] = "labelled_root_columns_reordered"
        else:
            array = np.asarray(risk_root, dtype=float)
            if supplied_codes is not None:
                positions = {code: index for index, code in enumerate(supplied_codes)}
                array = array[:, [positions[code] for code in codes]]
                diagnostics["asset_order_validation"] = "risk_asset_codes_reordered"
            else:
                diagnostics["asset_order_validation"] = (
                    "unlabelled_external_root_assumed_group_order"
                )
        if array.ndim != 2 or array.shape[1] != len(codes) or not np.isfinite(array).all():
            raise CSI500DataContractError("risk_provider_invalid_risk_root")
        return array, None, diagnostics
    if isinstance(covariance, pd.DataFrame):
        labelled_covariance = covariance.copy()
        labelled_covariance.index = labelled_covariance.index.astype(str)
        labelled_covariance.columns = labelled_covariance.columns.astype(str)
        if (
            set(labelled_covariance.index) != set(codes)
            or set(labelled_covariance.columns) != set(codes)
            or labelled_covariance.index.duplicated().any()
            or labelled_covariance.columns.duplicated().any()
        ):
            raise CSI500DataContractError(
                "risk_provider_labelled_covariance_axes_mismatch"
            )
        array = labelled_covariance.reindex(
            index=codes, columns=codes
        ).to_numpy(dtype=float)
        diagnostics["asset_order_validation"] = (
            "labelled_covariance_axes_reordered"
        )
    else:
        array = np.asarray(covariance, dtype=float)
        if supplied_codes is not None:
            positions = {code: index for index, code in enumerate(supplied_codes)}
            order = [positions[code] for code in codes]
            array = array[np.ix_(order, order)]
            diagnostics["asset_order_validation"] = "risk_asset_codes_reordered"
        else:
            diagnostics["asset_order_validation"] = (
                "unlabelled_external_covariance_assumed_group_order"
            )
    if array.shape != (len(codes), len(codes)) or not np.isfinite(array).all():
        raise CSI500DataContractError("risk_provider_invalid_annual_covariance")
    return None, array, diagnostics


def _numeric_quantiles(values: np.ndarray) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    if array.ndim != 1 or len(array) == 0 or not np.isfinite(array).all():
        raise CSI500DataContractError("capacity_quantiles_require_finite_vector")
    probabilities = (
        ("p00", 0.00), ("p10", 0.10), ("p25", 0.25), ("p50", 0.50),
        ("p75", 0.75), ("p90", 0.90), ("p100", 1.00),
    )
    return {
        label: float(np.quantile(array, probability))
        for label, probability in probabilities
    }


def _trade_limits(
    group: pd.DataFrame,
    config: CSI500StrategyConfig,
) -> tuple[dict[str, float], dict[str, float], dict[str, Any]]:
    _validate_config(config)
    raw_amount: np.ndarray | None = None
    amount_cny: np.ndarray | None = None
    participation_capacity_cny: np.ndarray | None = None
    uncapped_capacity_weight: np.ndarray | None = None
    explicit_columns = {
        "buy_limit_weight", "sell_limit_weight"
    }.issubset(group.columns)
    if (
        ("buy_limit_weight" in group.columns)
        != ("sell_limit_weight" in group.columns)
    ):
        raise CSI500DataContractError(
            "buy_and_sell_limit_override_columns_must_appear_together"
        )
    if explicit_columns:
        explicit_buy = pd.to_numeric(
            group["buy_limit_weight"], errors="coerce"
        ).to_numpy(dtype=float)
        explicit_sell = pd.to_numeric(
            group["sell_limit_weight"], errors="coerce"
        ).to_numpy(dtype=float)
        buy_finite = np.isfinite(explicit_buy)
        sell_finite = np.isfinite(explicit_sell)
        if np.any(buy_finite != sell_finite):
            raise CSI500DataContractError(
                "buy_and_sell_limit_override_rows_must_match"
            )
        explicit_mask = buy_finite & sell_finite
    else:
        explicit_buy = np.full(len(group), np.nan, dtype=float)
        explicit_sell = np.full(len(group), np.nan, dtype=float)
        explicit_mask = np.zeros(len(group), dtype=bool)

    if explicit_mask.all():
        buy = explicit_buy.copy()
        sell = explicit_sell.copy()
        method = "explicit_weight_limits"
    elif "amount" in group.columns:
        raw_amount = pd.to_numeric(
            group["amount"], errors="coerce"
        ).to_numpy(dtype=float)
        unresolved_amount = (
            (~np.isfinite(raw_amount) | (raw_amount <= 0.0))
            & ~explicit_mask
        )
        if np.any(unresolved_amount):
            raise CSI500DataContractError(
                "liquidity_amount_missing_or_nonpositive"
            )
        amount_for_capacity = raw_amount.copy()
        amount_for_capacity[explicit_mask] = np.where(
            np.isfinite(amount_for_capacity[explicit_mask])
            & (amount_for_capacity[explicit_mask] > 0.0),
            amount_for_capacity[explicit_mask],
            0.0,
        )
        amount_cny = (
            amount_for_capacity
            * float(config.liquidity_amount_to_cny_multiplier)
        )
        participation_capacity_cny = (
            amount_cny
            * int(config.trading_days_per_period)
            * float(config.max_adv_participation)
        )
        uncapped_capacity_weight = (
            participation_capacity_cny / float(config.portfolio_notional)
        )
        capacity = np.minimum(
            uncapped_capacity_weight, float(config.default_trade_limit)
        )
        buy = capacity.copy()
        sell = capacity.copy()
        buy[explicit_mask] = explicit_buy[explicit_mask]
        sell[explicit_mask] = explicit_sell[explicit_mask]
        method = (
            "adv_participation_weight_limits_cny_with_explicit_row_overrides"
            if explicit_mask.any()
            else "adv_participation_weight_limits_cny"
        )
    else:
        raise CSI500DataContractError(
            "trade_limits_require_explicit_weights_or_amount"
        )
    if not np.isfinite(buy).all() or not np.isfinite(sell).all():
        raise CSI500DataContractError("trade_limits_nonfinite")
    if np.any(buy < 0.0) or np.any(sell < 0.0):
        raise CSI500DataContractError("trade_limits_negative")
    suspended = (
        pd.to_numeric(group.get("is_suspended", 0.0), errors="coerce")
        .fillna(0.0).to_numpy(dtype=float) > 0.5
        if "is_suspended" in group else np.zeros(len(group), dtype=bool)
    )
    pressure = (
        pd.to_numeric(group.get("limit_pressure", 0.0), errors="coerce")
        .fillna(0.0).to_numpy(dtype=float)
        if "limit_pressure" in group else np.zeros(len(group), dtype=float)
    )
    pit_nontradable = (
        pd.to_numeric(group.get("pit_nontradable", 0.0), errors="coerce")
        .fillna(0.0).to_numpy(dtype=float) > 0.5
        if "pit_nontradable" in group
        else np.zeros(len(group), dtype=bool)
    )
    buy[pit_nontradable | suspended | (pressure >= 0.5)] = 0.0
    sell[pit_nontradable | suspended | (pressure <= -0.5)] = 0.0
    codes = group["ts_code"].astype(str).tolist()
    amount_audit: dict[str, Any] = {
        "raw_amount_unit": (
            str(config.liquidity_amount_unit).strip().lower()
            if raw_amount is not None
            else "not_applicable_explicit_weight_limits"
        ),
        "amount_to_cny_multiplier": (
            float(config.liquidity_amount_to_cny_multiplier)
            if raw_amount is not None else None
        ),
        "amount_unit_inferred": False,
        "capacity_currency": "CNY",
        "execution_days": int(config.trading_days_per_period),
        "max_adv_participation": float(config.max_adv_participation),
        "portfolio_notional_cny": float(config.portfolio_notional),
        "raw_amount_quantiles": (
            _numeric_quantiles(amount_for_capacity)
            if raw_amount is not None else None
        ),
        "amount_cny_quantiles": (
            _numeric_quantiles(amount_cny) if amount_cny is not None else None
        ),
        "participation_capacity_cny_quantiles": (
            _numeric_quantiles(participation_capacity_cny)
            if participation_capacity_cny is not None else None
        ),
        "uncapped_capacity_weight_quantiles": (
            _numeric_quantiles(uncapped_capacity_weight)
            if uncapped_capacity_weight is not None else None
        ),
        "final_buy_limit_weight_quantiles": _numeric_quantiles(buy),
        "final_sell_limit_weight_quantiles": _numeric_quantiles(sell),
    }
    return (
        dict(zip(codes, map(float, buy))),
        dict(zip(codes, map(float, sell))),
        {
            **amount_audit,
            "method": method,
            "explicit_override_count": int(np.sum(explicit_mask)),
            "explicit_zero_override_count": int(np.sum(
                explicit_mask & (explicit_buy == 0.0) & (explicit_sell == 0.0)
            )),
            "buy_zero_count": int(np.sum(buy == 0.0)),
            "sell_zero_count": int(np.sum(sell == 0.0)),
            "pit_nontradable_zero_count": int(np.sum(pit_nontradable)),
            "minimum_buy_limit": float(np.min(buy)),
            "minimum_sell_limit": float(np.min(sell)),
        },
    )


def _turnover(
    target: Mapping[str, float],
    previous: Mapping[str, float],
) -> float:
    codes = set(target) | set(previous)
    target_total = float(sum(target.values()))
    previous_total = float(sum(previous.values()))
    cash_target = max(0.0, 1.0 - target_total)
    cash_previous = max(0.0, 1.0 - previous_total)
    return 0.5 * (
        sum(abs(float(target.get(code, 0.0)) - float(previous.get(code, 0.0))) for code in codes)
        + abs(cash_target - cash_previous)
    )


def _direct_score_portfolio(
    candidate_codes: Sequence[str],
    group: pd.DataFrame,
    previous: Mapping[str, float],
    buy_limits: Mapping[str, float],
    sell_limits: Mapping[str, float],
    transaction_cost_rate: float,
) -> dict[str, Any]:
    """Build a score-only research comparator, never an executable order."""

    candidates = list(map(str, candidate_codes))
    blocked_base = {
        "status": "blocked",
        "tradable": False,
        "executable": False,
        "comparator_only": True,
        "research_use_only": True,
        "not_trading_advice": True,
        "weights": {},
        "trade_limit_violations": [],
        "trade_limit_violation_count": 0,
        "fallback_used": False,
        "not_optimizer_fallback": True,
    }
    if len(candidates) != 50 or len(set(candidates)) != 50:
        return {
            **blocked_base,
            "reason": "direct_baseline_requires_same_exact_50_support",
        }
    indexed = group.set_index("ts_code")
    if not set(candidates).issubset(indexed.index):
        return {
            **blocked_base,
            "reason": "candidate_missing_from_current_cross_section",
        }
    scores = indexed.loc[candidates, "alpha_score"].astype(float)
    strength = scores.rank(method="average", pct=True).to_numpy(dtype=float)
    if not np.isfinite(strength).all() or float(strength.sum()) <= 0.0:
        return {
            **blocked_base,
            "reason": "direct_score_has_no_positive_strength",
        }

    weights = {
        code: float(value)
        for code, value in zip(candidates, strength / strength.sum())
    }
    violations: list[str] = []
    for code in group["ts_code"].astype(str):
        trade = weights.get(code, 0.0) - float(previous.get(code, 0.0))
        if trade > float(buy_limits[code]) + 1.0e-10:
            violations.append(f"buy:{code}")
        if -trade > float(sell_limits[code]) + 1.0e-10:
            violations.append(f"sell:{code}")

    turnover = _turnover(weights, previous)
    executable = not violations
    return {
        "status": "ready",
        "tradable": False,
        "executable": executable,
        "comparator_only": True,
        "research_use_only": True,
        "not_trading_advice": True,
        "reason": (
            None if executable
            else "trade_limit_violations_recorded_for_comparator_only"
        ),
        "construction": "same_frozen_50_score_rank_proportional_long_only",
        "candidate_codes": candidates,
        "weights": weights,
        "holdings_count": len(weights),
        "one_way_turnover": turnover,
        "transaction_cost": transaction_cost_rate * turnover,
        "cost_scope": "hypothetical_comparator_turnover",
        "trade_limit_violations": violations,
        "trade_limit_violation_count": len(violations),
        "violations": violations,
        "fallback_used": False,
        "not_optimizer_fallback": True,
    }


def _portfolio_return(
    weights: Mapping[str, float],
    returns: Mapping[str, float],
) -> float:
    missing = [code for code, weight in weights.items() if weight > 0.0 and code not in returns]
    if missing:
        raise CSI500DataContractError(
            "realized_return_missing_for_holdings:" + ",".join(sorted(missing)[:20])
        )
    values = [float(weight) * _finite(returns[code], "realized_return") for code, weight in weights.items()]
    return float(sum(values))


def _drift_weights(
    weights: Mapping[str, float],
    returns: Mapping[str, float],
) -> dict[str, float]:
    if not weights:
        return {}
    gross = {
        code: float(weight) * (1.0 + _finite(returns[code], "realized_return"))
        for code, weight in weights.items() if code in returns
    }
    if len(gross) != len(weights) or any(value < 0.0 for value in gross.values()):
        raise CSI500DataContractError("cannot_drift_previous_holdings")
    total = float(sum(gross.values()))
    if total <= 0.0:
        raise CSI500DataContractError("drifted_holdings_have_nonpositive_value")
    return {code: value / total for code, value in gross.items()}


def _benchmark_mark_to_market_inputs(
    group: pd.DataFrame,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Return benchmark-only valuation inputs and explicit lineage."""

    if "benchmark_mark_to_market_return" in group.columns:
        column = "benchmark_mark_to_market_return"
        source = "database_qfq_close_mark_to_market"
        forward_valuation_only = True
    if (
        "official_index_return" in group.columns
        and pd.to_numeric(
            group["official_index_return"], errors="coerce"
        ).notna().all()
    ):
        value = float(pd.to_numeric(
            group["official_index_return"], errors="coerce"
        ).iloc[0])
        if not np.allclose(
            pd.to_numeric(group["official_index_return"], errors="coerce"),
            value, rtol=0.0, atol=1.0e-14,
        ):
            raise CSI500DataContractError(
                "official_index_return_not_constant_within_period"
            )
        values = {str(code): value for code in group["ts_code"]}
        return values, {
            "status": "ready",
            "source": "official_index_market_daily",
            "return_column": "official_index_return",
            "member_count": len(values),
            "finite_return_count": len(values),
            "missing_codes": [],
            "missing_code_count": 0,
            "official_index_scalar_return": value,
            "official_index_sources": sorted(set(
                group.get(
                    "official_index_source",
                    pd.Series("", index=group.index),
                ).astype(str)
            ) - {""}),
            "forward_valuation_only": True,
            "permitted_for_ic_label": False,
            "permitted_for_optimizer_or_comparator_realization": False,
        }
    elif "benchmark_mark_to_market_return" in group.columns:
        column = "benchmark_mark_to_market_return"
        source = "database_qfq_close_mark_to_market"
        forward_valuation_only = True
    else:
        # Unit/in-memory callers may predate the database-only valuation field.
        # The fallback is explicit and is never used by the database builder.
        column = "label_next_ret"
        source = "non_database_panel_label_compatibility"
        forward_valuation_only = False
    numeric = pd.to_numeric(group[column], errors="coerce")
    values = {
        str(code): float(value)
        for code, value in zip(group["ts_code"], numeric)
        if pd.notna(value) and math.isfinite(float(value))
    }
    all_codes = group["ts_code"].astype(str).tolist()
    missing_codes = sorted(set(all_codes) - set(values))
    audit: dict[str, Any] = {
        "status": "ready" if not missing_codes else "blocked",
        "source": source,
        "return_column": column,
        "member_count": len(all_codes),
        "finite_return_count": len(values),
        "missing_codes": missing_codes[:20],
        "missing_code_count": len(missing_codes),
        "forward_valuation_only": forward_valuation_only,
        "permitted_for_ic_label": False,
        "permitted_for_optimizer_or_comparator_realization": False,
    }
    if forward_valuation_only:
        statuses = group.get(
            "benchmark_mtm_status", pd.Series("", index=group.index)
        ).astype(str)
        reasons = group.get(
            "benchmark_mtm_reason", pd.Series("", index=group.index)
        ).fillna("").astype(str)
        staleness = pd.to_numeric(
            group.get(
                "benchmark_mtm_end_staleness_trading_days",
                pd.Series(np.nan, index=group.index),
            ),
            errors="coerce",
        )
        audit.update({
            "row_status_counts": {
                value: int((statuses == value).sum())
                for value in sorted(set(statuses)) if value
            },
            "blocked_reasons": {
                value: int((reasons == value).sum())
                for value in sorted(set(reasons)) if value
            },
            "maximum_end_quote_staleness_trading_days": (
                int(staleness.max()) if staleness.notna().any() else None
            ),
            "stale_price_carried_forward_count": int(pd.to_numeric(
                group.get(
                    "benchmark_mtm_stale_price_carried_forward",
                    pd.Series(False, index=group.index),
                ),
                errors="coerce",
            ).fillna(0).astype(bool).sum()),
        })
    return values, audit

def _global_top50_codes(group: pd.DataFrame) -> list[str]:
    ranked = group[["ts_code", "alpha_score"]].copy()
    if "pit_nontradable" in group.columns:
        tradable = ~pd.to_numeric(
            group["pit_nontradable"], errors="coerce"
        ).fillna(0).astype(bool)
        ranked = ranked.loc[tradable].copy()
    ranked["alpha_score"] = pd.to_numeric(ranked["alpha_score"], errors="coerce")
    if len(ranked) < 50 or not np.isfinite(
        ranked["alpha_score"].to_numpy(dtype=float)
    ).all():
        raise CSI500DataContractError(
            "global_top50_requires_at_least_50_finite_tradable_scores"
        )
    return ranked.sort_values(
        ["alpha_score", "ts_code"],
        ascending=[False, True],
        kind="mergesort",
    ).head(50)["ts_code"].astype(str).tolist()


def _carry_blocked_rebalance(
    period: dict[str, Any],
    group: pd.DataFrame,
    previous_portfolios: Mapping[str, Mapping[str, float]],
    config: CSI500StrategyConfig,
    holding_valuation_provider: HoldingValuationProvider | None = None,
) -> dict[str, dict[str, float]]:
    """Hold and mark prior portfolios when no new order may be emitted."""

    prior = {
        name: {str(code): float(weight) for code, weight in weights.items()}
        for name, weights in previous_portfolios.items()
    }
    period.update({
        "rebalance_blocked": True,
        "rebalance_status": "blocked_no_new_order",
        "no_new_orders": True,
        "optimized_order_weights": {},
        "direct_order_weights": {},
        "same_support_order_weights": {},
        "direct_comparator_weights": {},
        "same_support_comparator_weights": {},
    })
    required_portfolios = ("optimized", "direct")
    if any(not prior.get(name) for name in required_portfolios):
        period["carry_status"] = "not_available_before_all_portfolios_started"
        return prior

    returns = {
        str(code): float(value)
        for code, value in zip(group["ts_code"], group["label_next_ret"])
        if pd.notna(value) and math.isfinite(float(value))
    }
    benchmark_returns, benchmark_audit = (
        _benchmark_mark_to_market_inputs(group)
    )
    period["benchmark_valuation_audit"] = benchmark_audit
    missing = {
        name: sorted(set(weights) - set(returns))
        for name, weights in prior.items()
    }
    optional_same_support_missing = list(
        missing.get("same_support_score_weighted", [])
    )
    if optional_same_support_missing:
        prior["same_support_score_weighted"] = {}
        missing["same_support_score_weighted"] = []
        period.update({
            "same_support_status": "unavailable_missing_company_action_return",
            "same_support_missing_returns": optional_same_support_missing,
            "same_support_not_required_for_formal_optimizer_path": True,
        })
    portfolio_returns = {
        name: dict(returns) for name in prior
    }
    valuation_used = False
    if any(values for values in missing.values()):
        requested_missing = sorted({
            code for values in missing.values() for code in values
        })
        period["carry_missing_executable_returns"] = {
            name: values[:20] for name, values in missing.items() if values
        }
        if holding_valuation_provider is None:
            period.update({
                "carry_status": "blocked_missing_holding_returns",
                "carry_missing_returns": period[
                    "carry_missing_executable_returns"
                ],
                "holding_valuation_status": "provider_unavailable",
                "portfolio_state_advanced": False,
            })
            return prior
        maturities = sorted(group["maturity_date"].astype(str).unique())
        if len(maturities) != 1:
            period.update({
                "carry_status": "blocked_missing_holding_returns",
                "holding_valuation_status": "invalid_period_maturity",
                "portfolio_state_advanced": False,
            })
            return prior
        try:
            valuation = dict(holding_valuation_provider(
                str(period["signal_date"]), maturities[0], requested_missing
            ))
        except (CSI500DataContractError, ValueError, TypeError) as exc:
            period.update({
                "carry_status": "blocked_missing_holding_returns",
                "holding_valuation_status": "provider_blocked",
                "holding_valuation_reason": str(exc),
                "portfolio_state_advanced": False,
            })
            return prior
        period["holding_valuation_audit"] = valuation
        valuation_returns_raw = valuation.get("returns") or {}
        valuation_returns = {
            str(code): float(value)
            for code, value in dict(valuation_returns_raw).items()
            if math.isfinite(float(value)) and float(value) > -1.0
        }
        unexpected = sorted(set(valuation_returns) - set(requested_missing))
        unresolved = sorted(set(requested_missing) - set(valuation_returns))
        if (
            valuation.get("status") != "ready"
            or unexpected
            or unresolved
        ):
            period.update({
                "carry_status": "blocked_missing_holding_returns",
                "holding_valuation_status": "blocked",
                "holding_valuation_unexpected_codes": unexpected,
                "holding_valuation_unresolved_codes": unresolved,
                "portfolio_state_advanced": False,
            })
            return prior
        for name, codes in missing.items():
            portfolio_returns[name].update({
                code: valuation_returns[code] for code in codes
            })
        valuation_used = True
        period.update({
            "holding_valuation_status": "ready",
            "holding_valuation_used_codes_by_portfolio": {
                name: codes for name, codes in missing.items() if codes
            },
            "holding_valuation_usage_restricted_to_previous_holdings": True,
            "holding_valuation_permitted_for_new_positions": False,
            "holding_valuation_permitted_for_alpha_or_ic": False,
            "holding_valuation_permitted_for_comparator_establishment": False,
        })

    gross = {
        name: _portfolio_return(weights, portfolio_returns[name])
        for name, weights in prior.items() if weights
    }
    advanced = {
        name: _drift_weights(weights, portfolio_returns[name])
        if weights else {}
        for name, weights in prior.items()
    }
    same_support_available = bool(
        prior.get("same_support_score_weighted")
    )
    period.update({
        "carry_status": (
            "held_and_marked_to_market_with_prior_holding_valuation"
            if valuation_used else "held_and_marked_to_market"
        ),
        "portfolio_state_advanced": True,
        "carried_holdings_count": {
            name: len(weights) for name, weights in prior.items()
        },
        "optimized_gross_return": gross["optimized"],
        "optimized_return": gross["optimized"],
        "direct_gross_return": gross["direct"],
        "direct_return": gross["direct"],
        "same_support_gross_return": (
            gross.get("same_support_score_weighted")
        ),
        "same_support_return": gross.get(
            "same_support_score_weighted"
        ),
        "optimized_turnover": 0.0,
        "direct_turnover": 0.0,
        "same_support_turnover": 0.0,
        "optimized_cost": 0.0,
        "direct_cost": 0.0,
        "same_support_cost": 0.0,
        "ex_ante_tracking_error": None,
        "direct_status": "carried",
        "same_support_status": (
            "carried" if same_support_available else "unavailable"
        ),
    })

    codes = group["ts_code"].astype(str)
    benchmark = pd.to_numeric(group["benchmark_weight"], errors="coerce")
    exact_benchmark = (
        len(group) == int(config.expected_members)
        and codes.nunique() == int(config.expected_members)
        and np.isfinite(benchmark.to_numpy(dtype=float)).all()
        and bool((benchmark > 0.0).all())
        and abs(float(benchmark.sum()) - 1.0)
        <= float(config.normalized_weight_tolerance)
        and set(codes).issubset(benchmark_returns)
    )
    if not exact_benchmark:
        period.update({
            "status": "blocked",
            "evaluation_included": False,
            "carry_performance_status": (
                "blocked_without_complete_exact_500_benchmark_return"
            ),
            "benchmark_return_reason": (
                "benchmark_mark_to_market_return_missing_or_stale"
                if benchmark_audit["missing_code_count"]
                else "benchmark_cross_section_contract_incomplete"
            ),
        })
        return advanced

    benchmark_return = _portfolio_return(
        dict(zip(codes, benchmark.astype(float))), benchmark_returns
    )
    period.update({
        "status": "carried",
        "carry_performance_status": "included_complete_month",
        "benchmark_return": benchmark_return,
        "optimized_excess_return": gross["optimized"] - benchmark_return,
        "direct_excess_return": gross["direct"] - benchmark_return,
        "same_support_excess_return": (
            gross["same_support_score_weighted"] - benchmark_return
            if same_support_available else None
        ),
        "evaluation_included": True,
    })
    return advanced


def _split_name(signal_date: str, config: CSI500StrategyConfig) -> str:
    if signal_date <= _date_text(config.train_end):
        return "train"
    if signal_date <= _date_text(config.validation_end):
        return "validation"
    return "test_report_only"


def _max_drawdown(returns: Sequence[float]) -> float | None:
    if not returns:
        return None
    nav = np.cumprod(1.0 + np.asarray(returns, dtype=float))
    peaks = np.maximum.accumulate(nav)
    return float(np.min(nav / peaks - 1.0))


def _performance(
    returns: Sequence[float],
    benchmark: Sequence[float] | None,
    periods_per_year: int,
) -> dict[str, Any]:
    values = np.asarray(returns, dtype=float)
    if len(values) == 0:
        return {
            "periods": 0, "annual_return": None, "annual_volatility": None,
            "sharpe": None, "max_drawdown": None, "win_rate": None,
            "tracking_error": None, "information_ratio": None,
        }
    terminal = float(np.prod(1.0 + values))
    annual_return = terminal ** (periods_per_year / len(values)) - 1.0
    volatility = (
        float(values.std(ddof=1) * math.sqrt(periods_per_year))
        if len(values) > 1 else None
    )
    sharpe = (
        float(values.mean() / values.std(ddof=1) * math.sqrt(periods_per_year))
        if len(values) > 1 and values.std(ddof=1) > 1.0e-12 else None
    )
    tracking_error = None
    information_ratio = None
    if benchmark is not None:
        benchmark_values = np.asarray(benchmark, dtype=float)
        excess = values - benchmark_values
        if len(excess) > 1:
            tracking_error = float(excess.std(ddof=1) * math.sqrt(periods_per_year))
            if excess.std(ddof=1) > 1.0e-12:
                information_ratio = float(
                    excess.mean() / excess.std(ddof=1) * math.sqrt(periods_per_year)
                )
    return {
        "periods": int(len(values)),
        "annual_return": float(annual_return),
        "annual_volatility": volatility,
        "sharpe": sharpe,
        "max_drawdown": _max_drawdown(values.tolist()),
        "win_rate": float(np.mean(values > 0.0)),
        "tracking_error": tracking_error,
        "information_ratio": information_ratio,
    }


def run_csi500_strategy(
    panel: pd.DataFrame,
    *,
    config: CSI500StrategyConfig | None = None,
    risk_provider: RiskProvider | None = None,
    holding_valuation_provider: HoldingValuationProvider | None = None,
    optimizer: OptimizerCallable = optimize_stock_portfolio,
    persistence_connection: sqlite3.Connection | None = None,
    score_run_id: str | None = None,
    macro_monthly: pd.DataFrame | None = None,
    precomputed_score_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a causal monthly CSI500 benchmark/comparator/optimizer backtest."""

    config = config or CSI500StrategyConfig()
    _validate_config(config)
    frame = _normalize_panel(panel, config)
    timing_result = build_timing_overlay(
        frame,
        macro_monthly=macro_monthly,
        config=config.timing_overlay,
    )
    timing_by_signal = (
        timing_result.get('by_signal_date')
        if isinstance(timing_result, Mapping) else {}
    )
    timing_by_signal = timing_by_signal if isinstance(timing_by_signal, Mapping) else {}
    if str(config.score_source_mode) in {
        "precomputed_database", "factor_lab_champion"
    }:
        if precomputed_score_result is None:
            raise CSI500DataContractError(
                "precomputed_score_result_required_for_external_score_mode"
            )
        score_result = dict(precomputed_score_result)
    else:
        score_result = build_causal_icir_scores(frame, config=config)
    score_frame = score_result["scores"]
    run_id = score_run_id or _hash_parts(
        "csi500_score_run", asdict(config),
        frame[["signal_date", "maturity_date", "ts_code", *config.factor_columns]],
    )[:24]
    persisted = 0
    if (
        persistence_connection is not None
        and not score_frame.empty
        and str(config.score_source_mode) in {
            "causal_recompute", "factor_lab_champion"
        }
    ):
        persisted = persist_optimizer_factor_scores(
            persistence_connection, score_frame,
            score_run_id=run_id, score_name=config.score_name,
        )
    score_periods = {item["signal_date"]: item for item in score_result["periods"]}
    previous_optimized: dict[str, float] = {}
    previous_direct: dict[str, float] = {}
    previous_same_support: dict[str, float] = {}
    portfolio_state_valid = True
    period_results: list[dict[str, Any]] = []
    optimizer_attempts = 0
    optimizer_hits = 0

    def carry_current(period: dict[str, Any], group: pd.DataFrame) -> None:
        nonlocal previous_optimized, previous_direct, previous_same_support
        nonlocal portfolio_state_valid
        had_portfolios = bool(
            previous_optimized and previous_direct
        )
        advanced = _carry_blocked_rebalance(
            period,
            group,
            {
                "optimized": previous_optimized,
                "direct": previous_direct,
                "same_support_score_weighted": previous_same_support,
            },
            config,
            holding_valuation_provider,
        )
        previous_optimized = advanced["optimized"]
        previous_direct = advanced["direct"]
        previous_same_support = advanced["same_support_score_weighted"]
        if had_portfolios and period.get("portfolio_state_advanced") is False:
            portfolio_state_valid = False
        period_results.append(period)

    for signal_date, raw_group in frame.groupby("signal_date", sort=True):
        group = raw_group.sort_values("ts_code", kind="mergesort").reset_index(drop=True)
        phase = _split_name(str(signal_date), config)
        score_audit = score_periods.get(str(signal_date), {
            "status": "blocked", "reason": "score_period_missing"
        })
        period: dict[str, Any] = {
            "signal_date": str(signal_date),
            "phase": phase,
            "test_role": "report_only" if phase == "test_report_only" else "selection_allowed",
            "score_status": score_audit["status"],
            "score_reason": score_audit.get("reason"),
            "optimizer_status": "not_attempted",
            "optimized_order_weights": {},
            "direct_status": "not_attempted",
            "direct_order_weights": {},
            "direct_comparator_weights": {},
            "direct_definition": "global_top50_score_rank_proportional_long_only",
            "same_support_status": "not_attempted",
            "same_support_order_weights": {},
            "same_support_comparator_weights": {},
            "same_support_definition": (
                "optimizer_support_score_rank_proportional_long_only"
            ),
            "evaluation_included": False,
            "fallback_used": False,
        }
        if not portfolio_state_valid:
            period.update({
                "status": "blocked_state_continuity",
                "optimizer_status": "blocked",
                "optimizer_reason": "prior_holding_return_missing_state_unknown",
                "no_new_orders": True,
                "portfolio_state_advanced": False,
            })
            period_results.append(period)
            continue
        if score_audit["status"] != "ready":
            period["status"] = score_audit["status"]
            if previous_optimized:
                carry_current(period, group)
            else:
                period_results.append(period)
            continue
        current_scores = score_frame[score_frame["signal_date"] == signal_date]
        group = group.merge(
            current_scores[["ts_code", "score"]],
            on="ts_code", how="left", validate="one_to_one",
        ).rename(columns={"score": "alpha_score"})
        alpha_observed = pd.to_numeric(
            group.get(
                "alpha_view_observed",
                pd.Series(1, index=group.index),
            ),
            errors="coerce",
        ).fillna(1.0) > 0.5
        no_alpha_view_rows = ~alpha_observed
        missing_alpha = group["alpha_score"].isna()
        if missing_alpha.any():
            missing_observed_alpha = missing_alpha & alpha_observed
            no_alpha_view_alpha = missing_alpha & no_alpha_view_rows
            if missing_observed_alpha.any():
                period.update({
                    "status": "blocked", "optimizer_status": "blocked",
                    "optimizer_reason": "score_missing_for_current_member",
                    "score_missing_codes": group.loc[
                        missing_observed_alpha, "ts_code"
                    ].astype(str).tolist()[:20],
                })
                carry_current(period, group)
                continue
            finite_scores = pd.to_numeric(
                group.loc[~missing_alpha, "alpha_score"],
                errors="coerce",
            ).replace([np.inf, -np.inf], np.nan).dropna()
            if finite_scores.empty:
                period.update({
                    "status": "blocked", "optimizer_status": "blocked",
                    "optimizer_reason": "score_missing_for_all_tradable_members",
                })
                carry_current(period, group)
                continue
            score_floor = float(finite_scores.min()) - max(
                float(finite_scores.std(ddof=0))
                if len(finite_scores) > 1 else 0.0,
                1.0,
            )
            group.loc[no_alpha_view_alpha, "alpha_score"] = score_floor
            period["no_alpha_view_score_policy"] = {
                "status": "applied",
                "policy": "missing_no_alpha_view_receives_low_score_floor; new_buy_remains_zero_by_trade_limits",
                "score_floor": score_floor,
                "filled_count": int(no_alpha_view_alpha.sum()),
                "filled_codes": group.loc[
                    no_alpha_view_alpha, "ts_code"
                ].astype(str).tolist()[:20],
                "future_returns_used": False,
            }
        if no_alpha_view_rows.any():
            neutralized_style_columns: list[str] = []
            for column in config.style_columns:
                if column not in group.columns:
                    continue
                group[column] = pd.to_numeric(
                    group[column], errors="coerce"
                ).replace([np.inf, -np.inf], np.nan)
                group.loc[no_alpha_view_rows, column] = 0.0
                neutralized_style_columns.append(str(column))
            if neutralized_style_columns:
                period["no_alpha_view_style_policy"] = {
                    "status": "applied",
                    "policy": "alpha_view_observed_false_rows_receive_cross_section_neutral_style_exposure",
                    "neutralized_count": int(no_alpha_view_rows.sum()),
                    "neutralized_codes": group.loc[
                        no_alpha_view_rows, "ts_code"
                    ].astype(str).tolist()[:20],
                    "style_columns": neutralized_style_columns,
                    "future_returns_used": False,
                }
        timing_row_raw = timing_by_signal.get(str(signal_date))
        timing_row = (
            dict(timing_row_raw)
            if isinstance(timing_row_raw, Mapping) else None
        )
        if timing_row is not None:
            period['timing_overlay'] = timing_row
        adjusted_alpha, alpha_overlay_audit = apply_alpha_overlay(
            group,
            timing_row=timing_row,
            style_columns=config.style_columns,
            config=config.timing_overlay,
        )
        group['alpha_score'] = adjusted_alpha
        period['alpha_overlay_audit'] = alpha_overlay_audit
        try:
            risk_root, covariance, risk_audit = _resolve_risk(
                str(signal_date), group, frame, config, risk_provider
            )
            buy_limits, sell_limits, liquidity_audit = _trade_limits(group, config)
        except (CSI500DataContractError, ValueError, TypeError) as exc:
            period.update({
                "status": "blocked", "optimizer_status": "blocked",
                "optimizer_reason": str(exc),
            })
            carry_current(period, group)
            continue
        codes = group["ts_code"].astype(str).tolist()
        previous_current = {
            code: float(previous_optimized.get(code, 0.0)) for code in codes
        }
        executable_return_available = pd.to_numeric(
            group["label_next_ret"], errors="coerce"
        ).map(lambda value: pd.notna(value) and math.isfinite(float(value)))
        missing_executable_return_codes = sorted(
            group.loc[~executable_return_available, "ts_code"].astype(str).tolist()
        )
        if missing_executable_return_codes:
            for code in missing_executable_return_codes:
                buy_limits[code] = 0.0
            period["missing_executable_return_trade_policy"] = {
                "status": "applied",
                "policy": (
                    "missing_forward_executable_return_blocks_new_buys_"
                    "before_optimizer; existing_positions_may_only_not_increase_"
                    "and_require_holding_valuation"
                ),
                "buy_blocked_count": len(missing_executable_return_codes),
                "buy_blocked_codes": missing_executable_return_codes[:20],
                "future_returns_used": False,
            }
        base_blacklist = set(map(str, config.optimizer.blacklist))
        base_mandatory = set(map(str, config.optimizer.mandatory))
        for code in codes:
            previous_weight = previous_current[code]
            if previous_weight > sell_limits[code] + 1.0e-12:
                base_mandatory.add(code)
            if (
                previous_weight + buy_limits[code]
                < config.optimizer.min_weight - 1.0e-12
                and previous_weight <= sell_limits[code] + 1.0e-12
            ):
                base_blacklist.add(code)
        effective_turnover_limit = (
            float(config.optimizer.one_way_turnover_limit)
            if previous_optimized else 1.0
        )
        if not previous_optimized:
            period['turnover_cold_start_policy'] = {
                'status': 'initial_build_allows_full_one_way_turnover',
                'base_one_way_turnover_limit': float(
                    config.optimizer.one_way_turnover_limit
                ),
                'applied_one_way_turnover_limit': effective_turnover_limit,
                'reason': 'no_prior_live_optimizer_book',
            }
        period_optimizer_config = replace(
            config.optimizer,
            target_holdings=50,
            buy_limit=buy_limits,
            sell_limit=sell_limits,
            blacklist=tuple(sorted(base_blacklist)),
            mandatory=tuple(sorted(base_mandatory)),
            one_way_turnover_limit=effective_turnover_limit,
            transaction_cost_rate=config.transaction_cost_rate,
        )
        period_optimizer_config, timing_budget_audit = apply_timing_budget_to_optimizer_config(
            period_optimizer_config,
            timing_row=timing_row,
            config=config.timing_overlay,
        )
        period['timing_budget_audit'] = timing_budget_audit
        cross_section = group[[
            "ts_code", "alpha_score", "benchmark_weight", "industry"
        ]].copy()
        optimizer_style_columns = _effective_optimizer_style_columns(config)
        missing_optimizer_styles = [
            column for column in optimizer_style_columns if column not in group.columns
        ]
        if missing_optimizer_styles:
            period.update({
                "status": "blocked", "optimizer_status": "blocked",
                "optimizer_reason": (
                    "optimizer_style_columns_missing:"
                    + ",".join(missing_optimizer_styles)
                ),
            })
            carry_current(period, group)
            continue
        styles = group[["ts_code", *optimizer_style_columns]].copy()
        optimizer_attempts += 1
        optimizer_result = optimizer(
            cross_section,
            style_exposures=styles,
            annual_covariance=covariance,
            risk_root=risk_root,
            previous_weights=previous_optimized,
            config=period_optimizer_config,
        )
        period["optimizer_status"] = optimizer_result.get("status", "blocked")
        period["optimizer_reason"] = optimizer_result.get("reason")
        period["optimizer_result"] = optimizer_result
        period["risk_audit"] = risk_audit
        period["liquidity_audit"] = liquidity_audit
        if optimizer_result.get("status") != "ready" or not optimizer_result.get("tradable"):
            period["status"] = "blocked"
            carry_current(period, group)
            continue
        optimizer_hits += 1
        optimized_weights = {
            str(code): float(weight)
            for code, weight in optimizer_result["weights"].items()
            if float(weight) > config.optimizer.feasibility_tolerance
        }
        if len(optimized_weights) != 50:
            period.update({
                "status": "blocked", "optimizer_status": "blocked",
                "optimizer_reason": (
                    "certified_output_does_not_have_exactly_50_positive_weights"
                ),
                "optimized_order_weights": {},
            })
            carry_current(period, group)
            continue

        returns = {
            str(code): float(value)
            for code, value in zip(group["ts_code"], group["label_next_ret"])
            if pd.notna(value) and math.isfinite(float(value))
        }
        optimized_missing_returns = sorted(
            set(optimized_weights) - set(returns)
        )
        mandatory_codes = set(map(str, period_optimizer_config.mandatory))
        retained_valuation_codes: list[str] = []
        invalid_missing_return_codes: list[str] = []
        missing_return_audit: dict[str, dict[str, Any]] = {}
        for code in optimized_missing_returns:
            previous_weight = float(previous_optimized.get(code, 0.0))
            optimized_weight = float(optimized_weights[code])
            retained_mandatory = bool(
                previous_weight > config.optimizer.feasibility_tolerance
                and optimized_weight
                <= previous_weight + config.optimizer.feasibility_tolerance
            )
            missing_return_audit[code] = {
                "previous_weight": previous_weight,
                "optimized_weight": optimized_weight,
                "buy_limit": float(buy_limits.get(code, 0.0)),
                "mandatory": code in mandatory_codes,
                "retained_without_weight_increase": retained_mandatory,
                "new_position": previous_weight
                <= config.optimizer.feasibility_tolerance,
            }
            if retained_mandatory:
                retained_valuation_codes.append(code)
            else:
                invalid_missing_return_codes.append(code)
        if invalid_missing_return_codes:
            period.update({
                "status": "blocked_realization_precheck",
                "optimizer_status": "blocked_postsolve_realization",
                "optimizer_reason": (
                    "optimized_new_or_nonmandatory_holding_missing_"
                    "executable_return"
                ),
                "missing_return_position_audit": missing_return_audit,
                "holding_valuation_not_used_for_new_positions": True,
                "optimized_order_weights": {},
            })
            carry_current(period, group)
            continue
        candidates = list(
            optimizer_result["requested"]["selection"]["candidate_codes"]
        )
        try:
            global_top50 = _global_top50_codes(group)
        except CSI500DataContractError as exc:
            period.update({
                "status": "blocked_comparison",
                "direct_status": "blocked",
                "direct_reason": str(exc),
            })
            carry_current(period, group)
            continue
        direct_result = _direct_score_portfolio(
            global_top50, group, previous_direct, buy_limits, sell_limits,
            config.transaction_cost_rate,
        )
        same_support_result = _direct_score_portfolio(
            candidates, group, previous_same_support, buy_limits, sell_limits,
            config.transaction_cost_rate,
        )
        period["direct_status"] = direct_result["status"]
        period["direct_result"] = direct_result
        period["same_support_status"] = same_support_result["status"]
        period["same_support_result"] = same_support_result
        if (
            direct_result["status"] != "ready"
            or same_support_result["status"] != "ready"
        ):
            period["status"] = "blocked_comparison"
            carry_current(period, group)
            continue
        direct_weights = dict(direct_result["weights"])
        same_support_weights = dict(same_support_result["weights"])
        comparator_missing_returns = {
            "direct": sorted(set(direct_weights) - set(returns)),
            "same_support_score_weighted": sorted(
                set(same_support_weights) - set(returns)
            ),
        }
        if comparator_missing_returns["same_support_score_weighted"]:
            period["same_support_status"] = (
                "unavailable_missing_company_action_return"
            )
            period["same_support_result"] = {
                **same_support_result,
                "status": "unavailable",
                "reason": "missing_company_action_return",
            }
            same_support_weights = {}
        if comparator_missing_returns["direct"]:
            period.update({
                "direct_status": "unavailable_missing_company_action_return",
                "direct_reason": (
                    "comparator_establishment_missing_executable_return"
                ),
                "comparator_missing_returns": {
                    "direct": comparator_missing_returns["direct"]
                },
                "direct_not_required_for_formal_optimizer_path": True,
                "holding_valuation_not_used_for_comparator_establishment": True,
            })
            direct_weights = {}

        optimized_returns = dict(returns)
        if retained_valuation_codes:
            if holding_valuation_provider is None:
                period.update({
                    "status": "blocked_realization_precheck",
                    "optimizer_status": "blocked_postsolve_realization",
                    "optimizer_reason": (
                        "mandatory_retained_holding_valuation_provider_"
                        "unavailable"
                    ),
                    "missing_return_position_audit": missing_return_audit,
                })
                carry_current(period, group)
                continue
            maturity_values = sorted(
                group["maturity_date"].astype(str).unique()
            )
            try:
                valuation = dict(holding_valuation_provider(
                    str(signal_date), maturity_values[0],
                    retained_valuation_codes,
                ))
            except (CSI500DataContractError, ValueError, TypeError) as exc:
                valuation = {
                    "status": "blocked", "reason": str(exc), "returns": {}
                }
            period["retained_holding_valuation_audit"] = valuation
            valuation_returns = {
                str(code): float(value)
                for code, value in dict(
                    valuation.get("returns") or {}
                ).items()
                if math.isfinite(float(value)) and float(value) > -1.0
            }
            unexpected = sorted(
                set(valuation_returns) - set(retained_valuation_codes)
            )
            unresolved = sorted(
                set(retained_valuation_codes) - set(valuation_returns)
            )
            if (
                len(maturity_values) != 1
                or valuation.get("status") != "ready"
                or unexpected
                or unresolved
            ):
                period.update({
                    "status": "blocked_realization_precheck",
                    "optimizer_status": "blocked_postsolve_realization",
                    "optimizer_reason": (
                        "mandatory_retained_holding_valuation_blocked"
                    ),
                    "holding_valuation_unexpected_codes": unexpected,
                    "holding_valuation_unresolved_codes": unresolved,
                    "missing_return_position_audit": missing_return_audit,
                })
                carry_current(period, group)
                continue
            optimized_returns.update({
                code: valuation_returns[code]
                for code in retained_valuation_codes
            })
            period.update({
                "retained_holding_valuation_used_codes": (
                    retained_valuation_codes
                ),
                "holding_valuation_usage_restricted_to_previous_holdings": True,
                "holding_valuation_permitted_for_new_positions": False,
                "holding_valuation_permitted_for_alpha_or_ic": False,
            })
        benchmark_weights = dict(zip(
            group["ts_code"].astype(str),
            group["benchmark_weight"].astype(float),
        ))
        benchmark_returns, benchmark_audit = (
            _benchmark_mark_to_market_inputs(group)
        )
        period["benchmark_valuation_audit"] = benchmark_audit
        try:
            if benchmark_audit["missing_code_count"]:
                raise CSI500DataContractError(
                    "benchmark_mark_to_market_return_missing_or_stale:"
                    + ",".join(benchmark_audit["missing_codes"])
                )
            benchmark_return = _portfolio_return(
                benchmark_weights, benchmark_returns
            )
            optimized_gross = _portfolio_return(
                optimized_weights, optimized_returns
            )
            direct_gross = (
                _portfolio_return(direct_weights, returns)
                if direct_weights else None
            )
            same_support_gross = (
                _portfolio_return(same_support_weights, returns)
                if same_support_weights else None
            )
            optimized_return = optimized_gross - float(
                optimizer_result["realized"]["transaction_cost"]
            )
            direct_return = (
                direct_gross - float(direct_result["transaction_cost"])
                if direct_gross is not None else None
            )
            same_support_return = (
                same_support_gross - float(
                    same_support_result["transaction_cost"]
                )
                if same_support_gross is not None else None
            )
            next_optimized = _drift_weights(
                optimized_weights, optimized_returns
            )
            next_direct = (
                _drift_weights(direct_weights, returns)
                if direct_weights else {}
            )
            next_same_support = (
                _drift_weights(same_support_weights, returns)
                if same_support_weights else {}
            )
        except CSI500DataContractError as exc:
            period.update({
                "status": "blocked_realization",
                "realization_reason": str(exc),
                "optimized_order_weights": {},
                "direct_order_weights": {},
                "same_support_order_weights": {},
                "direct_comparator_weights": {},
                "same_support_comparator_weights": {},
                "portfolio_state_advanced": False,
            })
            portfolio_state_valid = False
            period_results.append(period)
            continue
        period.update({
            "status": "ready",
            "rebalance_blocked": False,
            "no_new_orders": False,
            "optimized_order_weights": optimized_weights,
            "direct_order_weights": {},
            "same_support_order_weights": {},
            "direct_comparator_weights": direct_weights,
            "same_support_comparator_weights": same_support_weights,
            "candidate_codes": candidates,
            "global_top50_codes": global_top50,
            "candidate_hash": optimizer_result["requested"]["selection"]["candidate_hash"],
            "benchmark_return": benchmark_return,
            "optimized_gross_return": optimized_gross,
            "optimized_return": optimized_return,
            "optimized_excess_return": optimized_return - benchmark_return,
            "direct_gross_return": direct_gross,
            "direct_return": direct_return,
            "direct_excess_return": (
                direct_return - benchmark_return
                if direct_return is not None else None
            ),
            "same_support_gross_return": same_support_gross,
            "same_support_return": same_support_return,
            "same_support_excess_return": (
                same_support_return - benchmark_return
                if same_support_return is not None else None
            ),
            "optimized_turnover": float(
                optimizer_result["realized"]["one_way_turnover"]
            ),
            "direct_turnover": (
                float(direct_result["one_way_turnover"])
                if direct_return is not None else None
            ),
            "same_support_turnover": (
                float(same_support_result["one_way_turnover"])
                if same_support_return is not None else None
            ),
            "optimized_cost": float(
                optimizer_result["realized"]["transaction_cost"]
            ),
            "direct_cost": (
                float(direct_result["transaction_cost"])
                if direct_return is not None else None
            ),
            "same_support_cost": (
                float(same_support_result["transaction_cost"])
                if same_support_return is not None else None
            ),
            "ex_ante_tracking_error": float(
                optimizer_result["realized"]["tracking_error"]
            ),
            "constraint_max_violation": float(
                optimizer_result["solver"]["max_constraint_violation"]
            ),
            "evaluation_included": True,
            "portfolio_state_advanced": True,
        })
        previous_optimized = next_optimized
        previous_direct = next_direct
        previous_same_support = next_same_support
        period_results.append(period)

    benchmark_nav = 1.0
    direct_nav = 1.0
    same_support_nav = 1.0
    optimized_nav = 1.0
    curve: list[dict[str, Any]] = []
    for period in period_results:
        if period.get("evaluation_included"):
            benchmark_nav *= 1.0 + period["benchmark_return"]
            if period.get("direct_return") is not None:
                direct_nav *= 1.0 + period["direct_return"]
                direct_nav_value: float | None = direct_nav
            else:
                direct_nav_value = None
            if period.get("same_support_return") is not None:
                same_support_nav *= 1.0 + period["same_support_return"]
                same_support_nav_value: float | None = same_support_nav
            else:
                same_support_nav_value = None
            optimized_nav *= 1.0 + period["optimized_return"]
            curve.append({
                "signal_date": period["signal_date"],
                "phase": period["phase"],
                "rebalance_status": period.get("rebalance_status", "executed"),
                "benchmark_nav": benchmark_nav,
                "direct_nav": direct_nav_value,
                "same_support_nav": same_support_nav_value,
                "optimized_nav": optimized_nav,
                "direct_excess_nav": (
                    direct_nav_value / benchmark_nav
                    if direct_nav_value is not None else None
                ),
                "same_support_excess_nav": (
                    same_support_nav_value / benchmark_nav
                    if same_support_nav_value is not None else None
                ),
                "optimized_excess_nav": optimized_nav / benchmark_nav,
                "benchmark_return": period["benchmark_return"],
                "direct_return": period["direct_return"],
                "same_support_return": period["same_support_return"],
                "optimized_return": period["optimized_return"],
                "direct_excess_return": period["direct_excess_return"],
                "same_support_excess_return": period["same_support_excess_return"],
                "optimized_excess_return": period["optimized_excess_return"],
                "optimized_turnover": period["optimized_turnover"],
                "direct_turnover": period["direct_turnover"],
                "same_support_turnover": period["same_support_turnover"],
                "optimized_cost": period["optimized_cost"],
                "direct_cost": period["direct_cost"],
                "same_support_cost": period["same_support_cost"],
                "ex_ante_tracking_error": period["ex_ante_tracking_error"],
            })

    def raw_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
        benchmark_returns = [item["benchmark_return"] for item in records]
        direct_records = [
            item for item in records
            if item.get("direct_return") is not None
        ]
        direct_returns = [item["direct_return"] for item in direct_records]
        direct_benchmark_returns = [
            item["benchmark_return"] for item in direct_records
        ]
        same_support_records = [
            item for item in records
            if item.get("same_support_return") is not None
        ]
        same_support_returns = [
            item["same_support_return"] for item in same_support_records
        ]
        same_support_benchmark_returns = [
            item["benchmark_return"] for item in same_support_records
        ]
        optimized_returns = [item["optimized_return"] for item in records]
        tracking_errors = [
            float(item["ex_ante_tracking_error"])
            for item in records
            if item.get("ex_ante_tracking_error") is not None
        ]
        return {
            "benchmark": _performance(
                benchmark_returns, None, config.periods_per_year
            ),
            "direct": {
                **_performance(
                    direct_returns, direct_benchmark_returns,
                    config.periods_per_year,
                ),
                "average_turnover": float(np.mean([
                    item["direct_turnover"] for item in direct_records
                    if item.get("direct_turnover") is not None
                ])) if direct_records else None,
                "total_cost": float(sum(
                    item["direct_cost"] for item in direct_records
                    if item.get("direct_cost") is not None
                )),
                "unavailable_periods": len(records) - len(direct_records),
                "definition": "global_top50_score_rank_proportional_long_only",
            },
            "same_support_score_weighted": {
                **_performance(
                    same_support_returns, same_support_benchmark_returns,
                    config.periods_per_year,
                ),
                "average_turnover": float(np.mean([
                    item["same_support_turnover"]
                    for item in same_support_records
                ])) if same_support_records else None,
                "total_cost": float(sum(
                    item["same_support_cost"]
                    for item in same_support_records
                )),
                "unavailable_periods": len(records) - len(
                    same_support_records
                ),
                "definition": (
                    "optimizer_support_score_rank_proportional_long_only"
                ),
            },
            "optimized": {
                **_performance(
                    optimized_returns, benchmark_returns,
                    config.periods_per_year,
                ),
                "average_turnover": float(np.mean([
                    item["optimized_turnover"] for item in records
                ])) if records else None,
                "total_cost": float(sum(
                    item["optimized_cost"] for item in records
                )),
                "average_ex_ante_tracking_error": (
                    float(np.mean(tracking_errors)) if tracking_errors else None
                ),
            },
        }

    def metrics_for_scope(scope: Sequence[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(scope, key=lambda item: item["signal_date"])
        empty = raw_metrics([])
        formal_start = (
            _date_text(config.formal_evaluation_start)
            if config.formal_evaluation_start is not None else None
        )
        pre_formal_history = []
        if formal_start is not None:
            pre_formal_history = [
                item for item in ordered
                if str(item.get("signal_date")) < formal_start
            ]
            ordered = [
                item for item in ordered
                if str(item.get("signal_date")) >= formal_start
            ]
        if not ordered:
            return {
                "performance_status": "unavailable_no_requested_periods",
                "requested_window_performance_status": (
                    "unavailable_no_requested_periods"
                ),
                "formal_metrics_valid": False,
                "continuity": {
                    "evaluated_periods": 0,
                    "complete_return_periods": 0,
                    "gap_periods": [],
                    "missing_calendar_months": [],
                    "formal_evaluation_start": formal_start,
                    "excluded_pre_formal_history_periods": len(
                        pre_formal_history
                    ),
                },
                **empty,
                "diagnostic_contiguous_segments": [],
                "longest_contiguous_segment": None,
            }

        return_fields = (
            "benchmark_return", "optimized_return",
        )

        def has_complete_four_strategy_returns(
            item: Mapping[str, Any],
        ) -> bool:
            if not item.get("evaluation_included"):
                return False
            return all(
                item.get(field_name) is not None
                and math.isfinite(float(item[field_name]))
                for field_name in return_fields
            )

        leading_warmup: list[dict[str, Any]] = []
        while (
            ordered
            and not has_complete_four_strategy_returns(ordered[0])
            and str(ordered[0].get("score_status") or "") == "warmup"
        ):
            leading_warmup.append(ordered.pop(0))
        if not ordered:
            return {
                "performance_status": "unavailable_causal_warmup_only",
                "requested_window_performance_status": (
                    "unavailable_causal_warmup_only"
                ),
                "formal_metrics_valid": False,
                "continuity": {
                    "evaluated_periods": 0,
                    "complete_return_periods": 0,
                    "gap_periods": [],
                    "missing_calendar_months": [],
                    "formal_evaluation_start": formal_start,
                    "excluded_pre_formal_history_periods": len(
                        pre_formal_history
                    ),
                    "excluded_leading_causal_warmup_periods": len(
                        leading_warmup
                    ),
                },
                **empty,
                "diagnostic_contiguous_segments": [],
                "longest_contiguous_segment": None,
            }

        records = [
            item for item in ordered
            if has_complete_four_strategy_returns(item)
        ]
        gap_periods = [
            item["signal_date"] for item in ordered
            if not has_complete_four_strategy_returns(item)
        ]
        month_by_date = {
            pd.Period(pd.to_datetime(item["signal_date"]), freq="M"):
                item["signal_date"]
            for item in ordered
        }
        expected_months = pd.period_range(
            min(month_by_date), max(month_by_date), freq="M"
        )
        missing_calendar_months = [
            str(month) for month in expected_months if month not in month_by_date
        ]
        requested_window_valid = bool(
            ordered
            and not gap_periods
            and not missing_calendar_months
            and not any(bool(item.get("rebalance_blocked")) for item in ordered)
        )

        segments: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        previous_month: pd.Period | None = None
        for item in ordered:
            month = pd.Period(pd.to_datetime(item["signal_date"]), freq="M")
            consecutive = (
                previous_month is None
                or month.ordinal == previous_month.ordinal + 1
            )
            if not has_complete_four_strategy_returns(item):
                if current:
                    segments.append(current)
                    current = []
                previous_month = month
                continue
            if not consecutive and current:
                segments.append(current)
                current = []
            current.append(item)
            previous_month = month
        if current:
            segments.append(current)
        diagnostic_segments = [
            {
                "start": segment[0]["signal_date"],
                "end": segment[-1]["signal_date"],
                "periods": len(segment),
                "diagnostic_only": True,
                "metrics": raw_metrics(segment),
            }
            for segment in segments
        ]
        longest_contiguous_segment = (
            max(
                diagnostic_segments,
                key=lambda segment: (segment["periods"], -int(segment["start"])),
            )
            if diagnostic_segments else None
        )
        continuity = {
            "window_start": ordered[0]["signal_date"],
            "window_end": ordered[-1]["signal_date"],
            "requested_data_start": (
                pre_formal_history[0]["signal_date"]
                if pre_formal_history else leading_warmup[0]["signal_date"]
                if leading_warmup else ordered[0]["signal_date"]
            ),
            "formal_evaluation_start": formal_start,
            "excluded_pre_formal_history_periods": len(pre_formal_history),
            "formal_evaluation_start_after_causal_warmup": (
                ordered[0]["signal_date"]
            ),
            "excluded_leading_causal_warmup_periods": len(leading_warmup),
            "window_period_rows": len(ordered),
            "requested_calendar_months": len(expected_months),
            "evaluated_periods": sum(
                bool(item.get("evaluation_included")) for item in ordered
            ),
            "complete_return_periods": len(records),
            "gap_periods": gap_periods,
            "missing_calendar_months": missing_calendar_months,
                "all_periods_have_complete_benchmark_and_optimizer": (
                    requested_window_valid
                ),
                "all_periods_have_complete_benchmark_and_three_portfolios": (
                    requested_window_valid
                    and all(
                        item.get("direct_return") is not None
                        and item.get("same_support_return") is not None
                        for item in records
                    )
                ),
            "carried_rebalance_periods_are_valid_when_returns_complete": True,
            "rebalance_blocked_periods": sum(
                bool(item.get("rebalance_blocked")) for item in ordered
            ),
        }
        continuity["carried_rebalance_periods_are_valid_when_returns_complete"] = (
            continuity["rebalance_blocked_periods"] == 0
        )
        raw = raw_metrics(records)
        if requested_window_valid:
            return {
                "performance_status": "valid_complete_requested_window",
                "requested_window_performance_status": (
                    "valid_complete_requested_window"
                ),
                "formal_metrics_valid": True,
                "continuity": continuity,
                **raw,
                "diagnostic_contiguous_segments": diagnostic_segments,
                "longest_contiguous_segment": longest_contiguous_segment,
            }

        invalidated: dict[str, Any] = {}
        invalid_fields = {
            "annual_return", "annual_volatility", "sharpe", "max_drawdown",
            "win_rate", "tracking_error", "information_ratio",
        }
        for name, payload in raw.items():
            invalidated[name] = dict(payload)
            invalidated[name]["observed_periods"] = payload.get("periods", 0)
            for field_name in invalid_fields:
                invalidated[name][field_name] = None
            invalidated[name]["formal_metric_status"] = (
                "invalid_incomplete_requested_window"
            )
        return {
            "performance_status": "invalid_incomplete_requested_window",
            "requested_window_performance_status": (
                "invalid_incomplete_requested_window"
            ),
            "formal_metrics_valid": False,
            "continuity": continuity,
            **invalidated,
            "diagnostic_contiguous_segments": diagnostic_segments,
            "longest_contiguous_segment": longest_contiguous_segment,
        }
    formal_start = (
        _date_text(config.formal_evaluation_start)
        if config.formal_evaluation_start is not None else None
    )

    def in_formal_window(item: Mapping[str, Any]) -> bool:
        return formal_start is None or str(item.get("signal_date")) >= formal_start

    formal_period_results = [
        period for period in period_results if in_formal_window(period)
    ]
    evaluated = [
        period for period in formal_period_results
        if period.get("evaluation_included")
    ]
    overall_metrics = metrics_for_scope(period_results)
    metrics_by_split = {
        split: metrics_for_scope([
            item for item in period_results if item["phase"] == split
        ])
        for split in ("train", "validation", "test_report_only")
    }
    formal_curve_valid = bool(overall_metrics["formal_metrics_valid"])
    for row in curve:
        row["formal_path_valid"] = formal_curve_valid
        row["diagnostic_only"] = not formal_curve_valid

    blocked_count = sum(
        str(item["status"]).startswith("blocked")
        for item in formal_period_results
    )
    rebalance_blocked_count = sum(
        bool(item.get("rebalance_blocked")) for item in formal_period_results
    )
    carried_count = sum(item["status"] == "carried" for item in formal_period_results)
    warmup_count = sum(item["status"] == "warmup" for item in formal_period_results)
    score_warmup_count = sum(item["status"] == "warmup" for item in period_results)
    pre_formal_history_count = len(period_results) - len(formal_period_results)
    formal_optimizer_attempts = sum(
        item.get("optimizer_status") not in {None, "not_attempted"}
        for item in formal_period_results
    )
    formal_optimizer_hits = sum(
        item.get("optimizer_status") == "ready"
        and isinstance(item.get("optimizer_result"), Mapping)
        and bool(item["optimizer_result"].get("tradable"))
        for item in formal_period_results
    )
    overall_status = (
        "blocked" if not evaluated
        else "partial" if blocked_count
        else "ready"
    )
    parameter_hash = _hash_parts(asdict(config))
    result_payload = {
        "schema_version": SCHEMA_VERSION,
        "status": overall_status,
        "tradable_period_count": sum(
            item["status"] == "ready" for item in formal_period_results
        ),
        "evaluated_period_count": len(evaluated),
        "carried_period_count": carried_count,
        "score_run_id": run_id,
        "persisted_score_rows": persisted,
        "score_contract": {
            "table": SCORE_TABLE,
            "score_rows": int(len(score_frame)),
            "point_in_time_policy": score_result["point_in_time_policy"],
            "warmup_periods": score_warmup_count,
            "formal_window_warmup_periods": warmup_count,
            "pre_formal_history_periods": pre_formal_history_count,
            "ic_weighting": (
                "factor_lab_champion_bridge_rolling_ols_adaptive_icir"
                if str(config.score_source_mode) == "factor_lab_champion"
                else {
                    "adaptive_bayesian_icir": (
                        "lowdin_orthogonalized_factorwise_empirical_bayes_icir"
                    ),
                    "precision": (
                        "causal_bayesian_mean_covariance_shrinkage_precision_cap"
                    ),
                    "walkforward_positive_ic": (
                        "validated_v10_positive_ic_hit_rate_stability_weighting"
                    ),
                }[config.factor_weight_method]
            ),
            "score_source_mode": str(config.score_source_mode),
            "factor_lab_profile": (
                str(config.factor_lab_profile)
                if str(config.score_source_mode) == "factor_lab_champion"
                else None
            ),
            "fallback_used": False,
        },
        "governance": {
            "parameter_hash": parameter_hash,
            "selection_splits": ["train", "validation"],
            "test_role": "report_only",
            "test_parameters_mutated": False,
            "effect_numbers_are_computed_not_assumed": True,
            "formal_metrics_require_contiguous_path": True,
            "formal_metrics_require_complete_post_warmup_window": True,
            "formal_evaluation_start": formal_start,
            "pre_formal_history_excluded_from_performance": True,
            "leading_causal_warmup_excluded_from_performance": True,
            "carried_rebalance_periods_counted_when_realized_returns_complete": True,
            "blocked_rebalance_policy": (
                "no_new_order;carry_prior_holdings;zero_cost;real_returns"
            ),
            "comparator_policy": {
                "direct": "global_top50_score_only",
                "same_support_score_weighted": "isolates_weight_optimization",
                "orders_emitted": False,
                "fallback_used": False,
            },
        },
        "periods": period_results,
        "curves": curve,
        "curve_status": (
            "formal_contiguous" if formal_curve_valid
            else "diagnostic_non_contiguous"
        ),
        "requested_window_performance_status": overall_metrics[
            "requested_window_performance_status"
        ],
        "metrics": overall_metrics,
        "metrics_by_split": metrics_by_split,
        "constraint_hit_rate": (
            formal_optimizer_hits / formal_optimizer_attempts
            if formal_optimizer_attempts else None
        ),
        "optimizer_hit_rate": (
            formal_optimizer_hits / formal_optimizer_attempts
            if formal_optimizer_attempts else None
        ),
        "optimizer_attempts": formal_optimizer_attempts,
        "optimizer_certified_periods": formal_optimizer_hits,
        "optimizer_attempts_including_pre_formal_history": optimizer_attempts,
        "optimizer_certified_periods_including_pre_formal_history": optimizer_hits,
        "blocked_periods": blocked_count,
        "rebalance_blocked_periods": rebalance_blocked_count,
        "score_frame": score_frame,
        "fallback_used": False,
    }
    result_payload['timing_overlay'] = timing_result
    result_payload.setdefault('governance', {})['timing_overlay_uses_future_returns'] = False
    return result_payload

def run_csi500_strategy_from_database(
    connection: sqlite3.Connection,
    *,
    start: str,
    end: str,
    max_months: int | None = None,
    config: CSI500StrategyConfig | None = None,
    risk_provider: RiskProvider | None = None,
    holding_valuation_provider: HoldingValuationProvider | None = None,
    persist_scores: bool = False,
    score_run_id: str | None = None,
) -> dict[str, Any]:
    config = config or CSI500StrategyConfig()
    try:
        panel, database_audit = build_csi500_panel_from_database(
            connection, start=start, end=end, max_months=max_months, config=config
        )
        effective_risk_provider = risk_provider
        if effective_risk_provider is None:
            effective_risk_provider = build_database_point_in_time_risk_provider(
                connection, panel, config=config
            )
            database_audit["risk_provider"] = dict(
                effective_risk_provider.prefetch_audit
            )
            database_audit["risk_provider"]["default_database_provider_used"] = True
        else:
            database_audit["risk_provider"] = {
                "provider": "caller_supplied",
                "default_database_provider_used": False,
            }
        effective_holding_valuation_provider = holding_valuation_provider
        if effective_holding_valuation_provider is None:
            effective_holding_valuation_provider = (
                DatabasePointInTimeHoldingValuationProvider(
                    connection, config=config
                )
            )
            database_audit["holding_valuation_provider"] = dict(
                effective_holding_valuation_provider.prefetch_audit
            )
            database_audit["holding_valuation_provider"][
                "default_database_provider_used"
            ] = True
        else:
            database_audit["holding_valuation_provider"] = {
                "provider": "caller_supplied",
                "default_database_provider_used": False,
                "usage_restricted_to_previous_holdings": True,
            }
        macro_monthly = None
        if bool(config.timing_overlay.enabled):
            try:
                macro_monthly = pd.read_sql_query(
                    'select * from macro_monthly order by month',
                    connection,
                )
            except Exception as exc:  # pragma: no cover - defensive DB contract
                raise CSI500DataContractError(
                    'macro_monthly_unavailable_for_timing_overlay:' + str(exc)
                ) from exc
            database_audit['macro_monthly'] = {
                'table': 'macro_monthly',
                'rows': int(len(macro_monthly)),
                'min_month': (
                    str(macro_monthly['month'].min())
                    if 'month' in macro_monthly.columns and not macro_monthly.empty else None
                ),
                'max_month': (
                    str(macro_monthly['month'].max())
                    if 'month' in macro_monthly.columns and not macro_monthly.empty else None
                ),
                'lag_policy': 'month<=signal_month_minus_one',
            }
        precomputed_score_result = None
        if str(config.score_source_mode) == "precomputed_database":
            precomputed_score_result = load_precomputed_optimizer_scores(
                connection, panel, config=config
            )
            database_audit["precomputed_score_source"] = dict(
                precomputed_score_result.get("score_source", {})
            )
        elif str(config.score_source_mode) == "factor_lab_champion":
            precomputed_score_result = build_factor_lab_champion_scores_from_database(
                connection, panel, config=config
            )
            database_audit["factor_lab_score_source"] = dict(
                precomputed_score_result.get("score_source", {})
            )
        result = run_csi500_strategy(
            panel,
            config=config,
            risk_provider=effective_risk_provider,
            holding_valuation_provider=(
                effective_holding_valuation_provider
            ),
            persistence_connection=connection if persist_scores else None,
            macro_monthly=macro_monthly,
            score_run_id=score_run_id,
            precomputed_score_result=precomputed_score_result,
        )
        result["database_audit"] = database_audit
        return result
    except (CSI500DataContractError, ValueError, TypeError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "tradable_period_count": 0,
            "reason": str(exc),
            "periods": [],
            "curves": [],
            "metrics": {},
            "weights": {},
            "fallback_used": False,
        }


__all__ = [
    "CSI500DataContractError",
    "CSI500StrategyConfig",
    "BAYESIAN_RESPONSIVE_FACTORS",
    "BAYESIAN_RESPONSIVE_SATELLITE_SCORE",
    "DatabasePointInTimeHoldingValuationProvider",
    "SCHEMA_VERSION",
    "SCORE_TABLE",
    "build_causal_icir_scores",
    "build_csi500_panel_from_database",
    "build_factor_lab_champion_scores_from_database",
    "create_optimizer_factor_score_table",
    "load_csi500_constituents",
    "load_precomputed_optimizer_scores",
    "optimizer_factor_score_table_ddl",
    "persist_optimizer_factor_scores",
    "DatabasePointInTimeRiskProvider",
    "build_database_point_in_time_risk_provider",
    "run_csi500_strategy",
    "run_csi500_strategy_from_database",
]
