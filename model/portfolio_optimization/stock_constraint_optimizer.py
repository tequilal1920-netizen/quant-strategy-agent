"""Strict benchmark-relative stock portfolio optimizer.

This module is intentionally independent from the existing ETF allocator.  It
implements the stock-level contract required by the factor laboratory:

1. solve the linear mandate jointly over continuous weights and binary support
   variables with SciPy/HiGHS MILP; and
2. freeze the exact support and solve the complete tracking-error-constrained
   SOCP on the *full* universe with Clarabel.

The MILP stage preserves every linear mandate (including exact cardinality,
industry/style exposure, turnover, buy/sell limits, mandatory names and
black/white lists).  It is time-boxed in production, but a time-limited HiGHS
incumbent is accepted only after independent linear residual checks; tracking
error is then verified on each proposed support with the original conic
constraint.  Trial weights are never returned.  The second stage is certified
only when Clarabel returns ``optimal`` and every independently recomputed
residual is inside tolerance.  There is no equal-weight, heuristic-support,
solver-routing, or other tradable fallback.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

try:  # Phase I must block explicitly when the HiGHS MILP API is unavailable.
    from scipy.optimize import Bounds as ScipyBounds
    from scipy.optimize import LinearConstraint as ScipyLinearConstraint
    from scipy.optimize import milp as scipy_milp
    from scipy.sparse import coo_matrix
except Exception:  # pragma: no cover - exercised only in an incomplete runtime.
    ScipyBounds = None
    ScipyLinearConstraint = None
    scipy_milp = None
    coo_matrix = None

try:  # The public contract blocks cleanly when the certified solver is absent.
    import cvxpy as cp
except Exception:  # pragma: no cover - exercised only in an incomplete runtime.
    cp = None


ENGINE_VERSION = "stock-constraint-optimizer/1.2-timeboxed-highs-milp-clarabel-socp"
SCHEMA_VERSION = "stock-constraint-solution/1.0"
SELECTION_METHOD = "highs_milp_timeboxed_feasible_support_then_clarabel_socp"
OPTIMIZER_FORM = "mixed_integer_linear_support_then_continuous_socp"


@dataclass(frozen=True)
class StockOptimizerConfig:
    """User-visible mandate for a long-only benchmark-relative portfolio."""

    target_holdings: int = 50
    min_weight: float = 0.002
    max_weight: float = 0.05
    max_active_weight: float = 0.03
    industry_deviation: float | Mapping[str, float] = 0.02
    style_bounds: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    default_style_bounds: tuple[float, float] = (-0.10, 0.10)
    target_tracking_error: float = 0.06
    one_way_turnover_limit: float = 1.00
    buy_limit: float | Mapping[str, float] | None = None
    sell_limit: float | Mapping[str, float] | None = None
    alpha_weight: float = 1.0
    alpha_scale: float = 0.05
    score_target_penalty: float = 0.0
    active_risk_penalty: float = 4.0
    transaction_cost_rate: float = 0.001
    turnover_l1_penalty: float = 0.001
    turnover_l2_penalty: float = 0.05
    blacklist: tuple[str, ...] = ()
    whitelist: tuple[str, ...] = ()
    mandatory: tuple[str, ...] = ()
    feasibility_tolerance: float = 2.0e-6
    solver_max_iterations: int = 500
    benchmark_weight_unit: str = "auto"
    benchmark_weight_sum_tolerance: float = 0.02
    candidate_style_balance_penalty: float = 0.25
    support_search_max_attempts: int = 3
    support_search_beam_width: int = 12
    milp_time_limit_seconds: float = 5.0
    allow_forced_retention_execution_exception: bool = True


@dataclass
class _PreparedProblem:
    frame: pd.DataFrame
    codes: list[str]
    industries: np.ndarray
    alpha: np.ndarray
    raw_alpha: np.ndarray
    benchmark: np.ndarray
    previous: np.ndarray
    outside_previous_weight: float
    outside_previous_positions: dict[str, float]
    cash_previous_weight: float
    styles: pd.DataFrame
    risk_root: np.ndarray
    risk_diagnostics: dict[str, Any]
    candidate_codes: list[str]
    candidate_index: np.ndarray
    noncandidate_index: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    active_weight_lower: np.ndarray
    active_weight_upper: np.ndarray
    execution_exceptions: tuple[dict[str, Any], ...]
    buy_limits: np.ndarray
    sell_limits: np.ndarray
    industry_limits: dict[str, float]
    style_bounds: dict[str, tuple[float, float]]
    config: StockOptimizerConfig
    requested: dict[str, Any]
    input_hash: str
    precheck: dict[str, Any]


def _finite_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}_must_be_numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name}_must_be_finite")
    return number

def _config_payload(config: StockOptimizerConfig | Mapping[str, Any] | None) -> dict[str, Any]:
    if config is None:
        return _canonical(asdict(StockOptimizerConfig()))
    if isinstance(config, StockOptimizerConfig):
        return _canonical(asdict(config))
    if isinstance(config, Mapping):
        return _canonical(dict(config))
    return {"invalid_config_type": type(config).__name__}


def _coerce_config(
    config: StockOptimizerConfig | Mapping[str, Any] | None,
) -> StockOptimizerConfig:
    if config is None:
        value = StockOptimizerConfig()
    elif isinstance(config, StockOptimizerConfig):
        value = config
    elif isinstance(config, Mapping):
        try:
            value = StockOptimizerConfig(**dict(config))
        except TypeError as exc:
            raise ValueError(f"invalid_config_fields:{exc}") from exc
    else:
        raise ValueError("config_must_be_StockOptimizerConfig_or_mapping")

    if isinstance(value.target_holdings, bool) or not isinstance(
        value.target_holdings, (int, np.integer)
    ):
        raise ValueError("target_holdings_must_be_integer")
    if int(value.target_holdings) <= 0:
        raise ValueError("target_holdings_must_be_positive")
    if isinstance(value.solver_max_iterations, bool) or not isinstance(
        value.solver_max_iterations, (int, np.integer)
    ):
        raise ValueError("solver_max_iterations_must_be_integer")
    if int(value.solver_max_iterations) <= 0:
        raise ValueError("solver_max_iterations_must_be_positive")
    for name in ("support_search_max_attempts", "support_search_beam_width"):
        candidate = getattr(value, name)
        if isinstance(candidate, bool) or not isinstance(candidate, (int, np.integer)):
            raise ValueError(f"{name}_must_be_integer")
        if int(candidate) <= 0:
            raise ValueError(f"{name}_must_be_positive")
    if _finite_float(value.feasibility_tolerance, "feasibility_tolerance") <= 0.0:
        raise ValueError("feasibility_tolerance_must_be_positive")
    unit = str(value.benchmark_weight_unit).strip().lower()
    if unit not in {"auto", "fraction", "percent"}:
        raise ValueError("benchmark_weight_unit_must_be_auto_fraction_or_percent")
    if _finite_float(
        value.benchmark_weight_sum_tolerance,
        "benchmark_weight_sum_tolerance",
    ) <= 0.0:
        raise ValueError("benchmark_weight_sum_tolerance_must_be_positive")
    if _finite_float(
        value.candidate_style_balance_penalty,
        "candidate_style_balance_penalty",
    ) < 0.0:
        raise ValueError("candidate_style_balance_penalty_must_be_nonnegative")
    if _finite_float(
        value.milp_time_limit_seconds,
        "milp_time_limit_seconds",
    ) < 0.0:
        raise ValueError("milp_time_limit_seconds_must_be_nonnegative")
    min_weight = _finite_float(value.min_weight, "min_weight")
    max_weight = _finite_float(value.max_weight, "max_weight")
    max_active = _finite_float(value.max_active_weight, "max_active_weight")
    if min_weight <= 0.0:
        raise ValueError("min_weight_must_be_strictly_positive_for_exact_cardinality")
    if max_weight < min_weight or max_weight > 1.0:
        raise ValueError("max_weight_must_be_between_min_weight_and_one")
    if max_active < 0.0:
        raise ValueError("max_active_weight_must_be_nonnegative")
    if _finite_float(value.alpha_scale, "alpha_scale") <= 0.0:
        raise ValueError("alpha_scale_must_be_positive")
    for name in (
        "target_tracking_error",
        "one_way_turnover_limit",
        "alpha_weight",
        "score_target_penalty",
        "active_risk_penalty",
        "transaction_cost_rate",
        "turnover_l1_penalty",
        "turnover_l2_penalty",
    ):
        if _finite_float(getattr(value, name), name) < 0.0:
            raise ValueError(f"{name}_must_be_nonnegative")
    if float(value.benchmark_weight_sum_tolerance) > 0.25:
        raise ValueError("benchmark_weight_sum_tolerance_too_large")
    return value


def _normalise_benchmark_weights(
    raw: np.ndarray,
    config: StockOptimizerConfig,
) -> tuple[np.ndarray, float, str]:
    total = float(raw.sum())
    if total <= 0.0:
        raise ValueError("benchmark_weight_sum_must_be_positive")
    tolerance = float(config.benchmark_weight_sum_tolerance)
    requested = str(config.benchmark_weight_unit).strip().lower()
    if requested == "auto":
        if abs(total - 1.0) <= tolerance:
            detected = "fraction"
        elif abs(total - 100.0) <= 100.0 * tolerance:
            detected = "percent"
        else:
            raise ValueError(
                "benchmark_weight_sum_not_close_to_one_or_one_hundred"
            )
    else:
        detected = requested
        target = 1.0 if detected == "fraction" else 100.0
        if abs(total - target) > target * tolerance:
            raise ValueError(
                f"benchmark_weight_sum_outside_{detected}_tolerance"
            )
    scaled = raw if detected == "fraction" else raw / 100.0
    scaled_total = float(scaled.sum())
    if scaled_total <= 0.0:
        raise ValueError("benchmark_weight_sum_must_be_positive")
    return scaled / scaled_total, total, detected


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (set, tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        return round(value, 14)
    return value


def _sha256_parts(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, np.ndarray):
            array = np.ascontiguousarray(part)
            digest.update(str(array.shape).encode("ascii"))
            digest.update(str(array.dtype).encode("ascii"))
            digest.update(array.tobytes())
        else:
            digest.update(
                json.dumps(
                    _canonical(part), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
    return digest.hexdigest()


def _normalise_alpha(
    values: pd.Series,
    benchmark: np.ndarray,
    scale: float,
) -> np.ndarray:
    """Linearly scale scores while preserving benchmark-weighted neutrality."""

    raw = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    weights = np.asarray(benchmark, dtype=float).reshape(-1)
    if raw.shape != weights.shape or not np.isfinite(raw).all():
        raise ValueError("alpha_score_must_be_finite_and_match_benchmark")
    if not np.isfinite(weights).all() or np.any(weights < 0.0):
        raise ValueError("benchmark_weights_invalid_for_alpha_normalization")
    total = float(weights.sum())
    if total <= 0.0:
        raise ValueError("benchmark_weight_sum_must_be_positive")
    weights = weights / total
    centered = raw - float(weights @ raw)
    dispersion = math.sqrt(float(weights @ np.square(centered)))
    if dispersion <= 1.0e-12:
        raise ValueError("alpha_score_has_no_cross_sectional_dispersion")
    return centered / dispersion * float(scale)


def _support_score_target(prepared: _PreparedProblem) -> np.ndarray:
    """Return the robust rank-proportional target on the certified support."""

    target = np.zeros(len(prepared.codes), dtype=float)
    indexes = prepared.candidate_index
    scores = pd.Series(prepared.raw_alpha[indexes], dtype=float)
    strength = scores.rank(method="average", pct=True).to_numpy(dtype=float)
    total = float(strength.sum())
    if not np.isfinite(strength).all() or total <= 0.0:
        raise ValueError("certified_support_score_target_unavailable")
    target[indexes] = strength / total
    return target


def _style_frame(
    frame: pd.DataFrame,
    style_exposures: pd.DataFrame | Mapping[str, Any] | Sequence[str] | None,
) -> pd.DataFrame:
    codes = frame["ts_code"].astype(str).tolist()
    if style_exposures is None:
        columns = [column for column in frame.columns if str(column).startswith("style_")]
        styles = frame[columns].copy() if columns else pd.DataFrame(index=range(len(frame)))
    elif isinstance(style_exposures, pd.DataFrame):
        styles = style_exposures.copy()
        if "ts_code" in styles.columns:
            styles["ts_code"] = styles["ts_code"].astype(str)
            if styles["ts_code"].duplicated().any():
                raise ValueError("style_exposures_duplicate_ts_code")
            styles = styles.set_index("ts_code").reindex(codes)
        elif list(styles.index.astype(str)) == codes:
            styles.index = range(len(styles))
        elif len(styles) != len(frame):
            raise ValueError("style_exposures_must_align_with_cross_section")
        else:
            styles.index = range(len(styles))
    elif isinstance(style_exposures, Mapping):
        styles = pd.DataFrame(style_exposures)
        if len(styles) != len(frame):
            raise ValueError("style_exposures_must_align_with_cross_section")
    else:
        columns = [str(column) for column in style_exposures]
        missing = [column for column in columns if column not in frame]
        if missing:
            raise ValueError("missing_style_columns:" + ",".join(missing))
        styles = frame[columns].copy()
    styles.columns = [str(column) for column in styles.columns]
    if len(set(styles.columns)) != len(styles.columns):
        raise ValueError("duplicate_style_names")
    for column in styles:
        styles[column] = pd.to_numeric(styles[column], errors="coerce")
    if styles.isna().any().any() or not np.isfinite(styles.to_numpy(dtype=float)).all():
        raise ValueError("style_exposures_contain_missing_or_nonfinite_values")
    styles.index = range(len(frame))
    return styles.astype(float)


def _risk_root_from_inputs(
    n: int,
    annual_covariance: np.ndarray | pd.DataFrame | None,
    risk_root: np.ndarray | pd.DataFrame | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    if (annual_covariance is None) == (risk_root is None):
        raise ValueError("provide_exactly_one_of_annual_covariance_or_risk_root")
    if risk_root is not None:
        root = np.asarray(risk_root, dtype=float)
        if root.ndim != 2 or root.shape[1] != n:
            raise ValueError("risk_root_must_have_n_assets_columns")
        if not np.isfinite(root).all():
            raise ValueError("risk_root_contains_nonfinite_values")
        return root, {
            "input": "risk_root",
            "factor_count": int(root.shape[0]),
            "asset_count": n,
            "psd_by_construction": True,
        }

    covariance = np.asarray(annual_covariance, dtype=float)
    if covariance.shape != (n, n):
        raise ValueError("annual_covariance_must_be_n_by_n")
    if not np.isfinite(covariance).all():
        raise ValueError("annual_covariance_contains_nonfinite_values")
    symmetry_error = float(np.max(np.abs(covariance - covariance.T)))
    if symmetry_error > 1.0e-8:
        raise ValueError("annual_covariance_is_not_symmetric")
    covariance = (covariance + covariance.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    scale = max(float(np.max(np.abs(eigenvalues))), 1.0)
    if float(eigenvalues.min()) < -1.0e-9 * scale:
        raise ValueError("annual_covariance_is_not_psd")
    clipped = np.maximum(eigenvalues, 0.0)
    root = np.sqrt(clipped)[:, None] * eigenvectors.T
    return root, {
        "input": "annual_covariance",
        "asset_count": n,
        "minimum_eigenvalue": float(eigenvalues.min()),
        "maximum_eigenvalue": float(eigenvalues.max()),
        "symmetry_error": symmetry_error,
        "tiny_negative_eigenvalues_clipped": int(np.sum(eigenvalues < 0.0)),
        "psd_by_construction": True,
    }

def _strict_style_frame(
    frame: pd.DataFrame,
    style_exposures: pd.DataFrame | Mapping[str, Any] | Sequence[str] | None,
) -> pd.DataFrame:
    """Align every external style exposure by security code, never by row count."""

    codes = frame["ts_code"].astype(str).tolist()
    code_set = set(codes)
    if isinstance(style_exposures, pd.DataFrame):
        supplied = style_exposures.copy()
        if "ts_code" not in supplied.columns:
            index_codes = supplied.index.astype(str).tolist()
            if len(set(index_codes)) != len(index_codes):
                raise ValueError("style_exposures_duplicate_ts_code_index")
            if set(index_codes) != code_set:
                raise ValueError(
                    "style_exposures_dataframe_requires_ts_code_or_exact_code_index"
                )
            supplied.index = index_codes
            supplied = supplied.reindex(codes).reset_index(drop=True)
        return _style_frame(frame, supplied)

    if isinstance(style_exposures, Mapping):
        aligned: dict[str, list[float]] = {}
        for raw_name, raw_values in style_exposures.items():
            name = str(raw_name)
            if isinstance(raw_values, pd.Series):
                series = raw_values.copy()
                series.index = series.index.astype(str)
                if series.index.duplicated().any() or set(series.index) != code_set:
                    raise ValueError(
                        f"style_exposure_requires_exact_code_index:{name}"
                    )
                aligned[name] = series.reindex(codes).tolist()
            elif isinstance(raw_values, Mapping):
                values = {str(key): value for key, value in raw_values.items()}
                if set(values) != code_set:
                    raise ValueError(
                        f"style_exposure_requires_exact_code_mapping:{name}"
                    )
                aligned[name] = [values[code] for code in codes]
            else:
                raise ValueError(
                    f"style_exposure_mapping_must_be_code_keyed:{name}"
                )
        supplied = pd.DataFrame({"ts_code": codes, **aligned})
        return _style_frame(frame, supplied)

    return _style_frame(frame, style_exposures)


def _aligned_risk_root_from_inputs(
    input_codes: Sequence[str],
    sorted_codes: Sequence[str],
    annual_covariance: np.ndarray | pd.DataFrame | None,
    risk_root: np.ndarray | pd.DataFrame | None,
    risk_asset_codes: Sequence[str] | None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Align a labelled matrix, or an ndarray plus its explicit/source row order."""

    codes = list(map(str, sorted_codes))
    code_set = set(codes)
    if isinstance(annual_covariance, pd.DataFrame):
        covariance = annual_covariance.copy()
        covariance.index = covariance.index.astype(str)
        covariance.columns = covariance.columns.astype(str)
        if covariance.index.duplicated().any() or covariance.columns.duplicated().any():
            raise ValueError("annual_covariance_duplicate_asset_labels")
        if set(covariance.index) != code_set or set(covariance.columns) != code_set:
            raise ValueError("annual_covariance_asset_labels_must_match_cross_section")
        aligned_covariance = covariance.reindex(index=codes, columns=codes)
        root, diagnostics = _risk_root_from_inputs(
            len(codes), aligned_covariance, None
        )
    elif isinstance(risk_root, pd.DataFrame):
        root_frame = risk_root.copy()
        root_frame.columns = root_frame.columns.astype(str)
        if root_frame.columns.duplicated().any():
            raise ValueError("risk_root_duplicate_asset_labels")
        if set(root_frame.columns) != code_set:
            raise ValueError("risk_root_asset_labels_must_match_cross_section")
        aligned_root = root_frame.reindex(columns=codes)
        root, diagnostics = _risk_root_from_inputs(
            len(codes), None, aligned_root
        )
    else:
        source_codes = list(
            map(str, risk_asset_codes if risk_asset_codes is not None else input_codes)
        )
        if len(set(source_codes)) != len(source_codes) or set(source_codes) != code_set:
            raise ValueError("risk_asset_codes_must_match_cross_section_exactly")
        position = {code: index for index, code in enumerate(source_codes)}
        order = [position[code] for code in codes]
        if annual_covariance is not None:
            raw_covariance = np.asarray(annual_covariance, dtype=float)
            if raw_covariance.shape != (len(source_codes), len(source_codes)):
                raise ValueError("annual_covariance_must_be_n_by_n")
            aligned_covariance = raw_covariance[np.ix_(order, order)]
            root, diagnostics = _risk_root_from_inputs(
                len(codes), aligned_covariance, None
            )
        else:
            raw_root = np.asarray(risk_root, dtype=float)
            if raw_root.ndim != 2 or raw_root.shape[1] != len(source_codes):
                raise ValueError("risk_root_must_have_n_assets_columns")
            aligned_root = raw_root[:, order]
            root, diagnostics = _risk_root_from_inputs(
                len(codes), None, aligned_root
            )
    diagnostics = {
        **diagnostics,
        "asset_order_verified": True,
        "asset_order_hash": _sha256_parts(codes),
    }
    return root, diagnostics


def _limits_by_code(
    value: float | Mapping[str, float] | None,
    codes: Sequence[str],
    label: str,
) -> np.ndarray:
    if value is None:
        return np.full(len(codes), np.inf, dtype=float)
    if isinstance(value, Mapping):
        output = np.full(len(codes), np.inf, dtype=float)
        position = {code: index for index, code in enumerate(codes)}
        unknown = sorted(set(map(str, value)) - set(position))
        if unknown:
            raise ValueError(f"{label}_contains_unknown_codes:" + ",".join(unknown))
        for raw_code, raw_limit in value.items():
            limit = _finite_float(raw_limit, label)
            if limit < 0.0:
                raise ValueError(f"{label}_must_be_nonnegative")
            output[position[str(raw_code)]] = limit
        return output
    limit = _finite_float(value, label)
    if limit < 0.0:
        raise ValueError(f"{label}_must_be_nonnegative")
    return np.full(len(codes), limit, dtype=float)


def _industry_limits(
    industries: Sequence[str],
    value: float | Mapping[str, float],
) -> dict[str, float]:
    unique = list(dict.fromkeys(map(str, industries)))
    if isinstance(value, Mapping):
        raw = {str(key): _finite_float(limit, "industry_deviation") for key, limit in value.items()}
        missing = [industry for industry in unique if industry not in raw]
        unknown = [industry for industry in raw if industry not in unique]
        if missing:
            raise ValueError("industry_deviation_missing_industries:" + ",".join(missing))
        if unknown:
            raise ValueError("industry_deviation_unknown_industries:" + ",".join(unknown))
        limits = {industry: raw[industry] for industry in unique}
    else:
        limit = _finite_float(value, "industry_deviation")
        limits = {industry: limit for industry in unique}
    if any(limit < 0.0 for limit in limits.values()):
        raise ValueError("industry_deviation_must_be_nonnegative")
    return limits


def _resolved_style_bounds(
    styles: pd.DataFrame,
    config: StockOptimizerConfig,
) -> dict[str, tuple[float, float]]:
    supplied = {str(key): tuple(value) for key, value in config.style_bounds.items()}
    unknown = sorted(set(supplied) - set(styles.columns))
    if unknown:
        raise ValueError("style_bounds_unknown_styles:" + ",".join(unknown))
    default = tuple(config.default_style_bounds)
    if len(default) != 2:
        raise ValueError("default_style_bounds_must_have_two_values")
    output: dict[str, tuple[float, float]] = {}
    for name in styles.columns:
        raw = supplied.get(name, default)
        if len(raw) != 2:
            raise ValueError(f"style_bound_must_have_two_values:{name}")
        lower = _finite_float(raw[0], f"style_lower_{name}")
        upper = _finite_float(raw[1], f"style_upper_{name}")
        if lower > upper:
            raise ValueError(f"style_lower_exceeds_upper:{name}")
        output[name] = (lower, upper)
    return output


def _candidate_support(
    frame: pd.DataFrame,
    benchmark: np.ndarray,
    industry_limits: Mapping[str, float],
    config: StockOptimizerConfig,
    previous: np.ndarray,
    buy_limits: np.ndarray,
    sell_limits: np.ndarray,
    styles: pd.DataFrame,
) -> tuple[list[str], dict[str, Any]]:
    codes = frame["ts_code"].astype(str).tolist()
    code_set = set(codes)
    blacklist = set(map(str, config.blacklist))
    whitelist = set(map(str, config.whitelist))
    mandatory = set(map(str, config.mandatory))
    unknown = sorted((blacklist | whitelist | mandatory) - code_set)
    if unknown:
        raise ValueError("mandate_contains_unknown_codes:" + ",".join(unknown))
    if mandatory & blacklist:
        raise ValueError("mandatory_intersects_blacklist")
    if whitelist and not mandatory.issubset(whitelist):
        raise ValueError("mandatory_must_be_inside_whitelist")

    min_weight = _finite_float(config.min_weight, "min_weight")
    max_weight = _finite_float(config.max_weight, "max_weight")
    max_active = _finite_float(config.max_active_weight, "max_active_weight")
    upper_capacity = np.minimum.reduce([
        np.full(len(codes), max_weight),
        benchmark + max_active,
        previous + buy_limits,
        np.ones(len(codes)),
    ])
    capacity_eligible = {
        code for code, capacity in zip(codes, upper_capacity)
        if float(capacity) >= min_weight - 1.0e-12
    }
    eligible = [
        code for code in codes
        if code in capacity_eligible
        and code not in blacklist
        and (not whitelist or code in whitelist)
    ]
    target = int(config.target_holdings)
    if target <= 0:
        raise ValueError("target_holdings_must_be_positive")
    if len(eligible) < target:
        raise ValueError("eligible_universe_smaller_than_target_holdings")

    code_position = {code: index for index, code in enumerate(codes)}
    # A security whose benchmark weight exceeds the active cap cannot be set to
    # zero.  It is therefore part of the frozen support before alpha ranking.
    active_cap_required = {
        code for code, weight in zip(codes, benchmark)
        if float(weight) > float(config.max_active_weight) + 1.0e-12
    }
    retention_required = {
        code for code, held, sell_limit in zip(codes, previous, sell_limits)
        if float(held - sell_limit) > 1.0e-12
    }
    required_codes = mandatory | active_cap_required | retention_required
    if not required_codes.issubset(set(eligible)):
        raise ValueError("active_cap_mandatory_or_sell_locked_security_not_eligible")
    if len(required_codes) > target:
        raise ValueError("required_security_count_exceeds_target_holdings")

    industry_by_code = dict(zip(codes, frame["industry"].astype(str)))
    benchmark_by_industry = {
        industry: float(benchmark[frame["industry"].astype(str).to_numpy() == industry].sum())
        for industry in dict.fromkeys(frame["industry"].astype(str).tolist())
    }
    required_industries = {
        industry for industry, weight in benchmark_by_industry.items()
        if weight > industry_limits[industry] + 1.0e-12
    }
    eligible_by_industry: dict[str, list[str]] = {}
    for code in eligible:
        eligible_by_industry.setdefault(industry_by_code[code], []).append(code)
    missing_industries = sorted(required_industries - set(eligible_by_industry))
    if missing_industries:
        raise ValueError("industry_constraint_has_no_eligible_security:" + ",".join(missing_industries))

    alpha_series = frame["alpha_score"].astype(float)
    alpha_by_code = dict(zip(codes, alpha_series))
    alpha_rank_by_code = dict(zip(
        codes, alpha_series.rank(method="average", pct=True).to_numpy(dtype=float)
    ))
    if len(styles.columns):
        style_values = styles.to_numpy(dtype=float)
        benchmark_style = benchmark @ style_values
        scale = np.std(style_values, axis=0, ddof=0)
        scale = np.where(scale > 1.0e-8, scale, 1.0)
        distance = np.mean(
            np.square((style_values - benchmark_style) / scale), axis=1
        )
    else:
        distance = np.zeros(len(codes), dtype=float)
    style_distance_by_code = dict(zip(codes, distance))
    capacity_scale = max(float(np.max(upper_capacity)), 1.0e-12)
    capacity_by_code = dict(zip(codes, upper_capacity / capacity_scale))
    balance_penalty = float(config.candidate_style_balance_penalty)
    priority_by_code = {
        code: alpha_rank_by_code[code]
        - balance_penalty * style_distance_by_code[code]
        + 0.05 * capacity_by_code[code]
        for code in codes
    }
    for values in eligible_by_industry.values():
        values.sort(
            key=lambda code: (
                -priority_by_code[code], -alpha_by_code[code], code
            )
        )

    quotas = {industry: 0 for industry in eligible_by_industry}
    for code in required_codes:
        industry = industry_by_code[code]
        quotas[industry] = quotas.get(industry, 0) + 1
    for industry in required_industries:
        quotas[industry] = max(quotas.get(industry, 0), 1)
    for industry, members in eligible_by_industry.items():
        required_weight = max(
            0.0,
            benchmark_by_industry.get(industry, 0.0)
            - float(industry_limits[industry]),
        )
        capacities = sorted(
            (float(upper_capacity[code_position[code]]) for code in members),
            reverse=True,
        )
        cumulative = np.cumsum(capacities)
        if not len(cumulative) or float(cumulative[-1]) < required_weight - 1.0e-12:
            raise ValueError(
                f"industry_upper_capacity_below_required_weight:{industry}"
            )
        minimum_names = (
            0
            if required_weight <= 1.0e-12
            else int(np.searchsorted(cumulative, required_weight, side="left") + 1)
        )
        quotas[industry] = max(quotas.get(industry, 0), minimum_names)
    if sum(quotas.values()) > target:
        raise ValueError("industry_coverage_and_mandatory_count_exceed_target_holdings")

    # D'Hondt-style deterministic apportionment keeps support approximately
    # proportional to benchmark industry weights while respecting capacity.
    while sum(quotas.values()) < target:
        choices = [
            industry for industry, members in eligible_by_industry.items()
            if quotas.get(industry, 0) < len(members)
        ]
        if not choices:
            raise ValueError("unable_to_allocate_target_holdings")
        industry = max(
            choices,
            key=lambda item: (
                benchmark_by_industry.get(item, 0.0) / (quotas.get(item, 0) + 1.0),
                priority_by_code[eligible_by_industry[item][quotas.get(item, 0)]],
                item,
            ),
        )
        quotas[industry] = quotas.get(industry, 0) + 1

    selected = set(required_codes)
    for industry, quota in quotas.items():
        members = eligible_by_industry[industry]
        already = [code for code in members if code in selected]
        needed = max(0, quota - len(already))
        for code in members:
            if needed == 0:
                break
            if code not in selected:
                selected.add(code)
                needed -= 1

    if len(selected) < target:
        remaining = sorted(
            (code for code in eligible if code not in selected),
            key=lambda code: (-priority_by_code[code], -alpha_by_code[code], code),
        )
        selected.update(remaining[: target - len(selected)])
    if len(selected) > target:
        optional = sorted(
            (code for code in selected if code not in required_codes),
            key=lambda code: (priority_by_code[code], alpha_by_code[code], code),
        )
        selected.difference_update(optional[: len(selected) - target])
    if len(selected) != target or not required_codes.issubset(selected):
        raise ValueError("candidate_support_cardinality_failure")

    ordered = sorted(selected, key=lambda code: (-alpha_by_code[code], code))
    return ordered, {
        "method": "industry_coverage_aware_initial_support_for_diagnostics",
        "phase_i_method": SELECTION_METHOD,
        "optimizer_form": OPTIMIZER_FORM,
        "is_mixed_integer": True,
        "target_holdings": target,
        "candidate_count": len(ordered),
        "candidate_codes": ordered,
        "candidate_hash": _sha256_parts(ordered),
        "industry_quotas": {key: int(value) for key, value in sorted(quotas.items())},
        "mandatory_codes": sorted(mandatory),
        "required_codes": sorted(required_codes),
        "active_cap_required_codes": sorted(active_cap_required),
        "sell_locked_required_codes": sorted(retention_required),
        "eligible_codes": sorted(eligible),
        "capacity_ineligible_codes": sorted(code_set - capacity_eligible),
        "candidate_style_balance_penalty": balance_penalty,
        "selection_priority": "alpha_rank_minus_style_distance_plus_capacity",
        "blacklist": sorted(blacklist),
        "whitelist": sorted(whitelist),
    }


def _relaxation_hint(code: str, message: str, amount: float | None = None) -> dict[str, Any]:
    return {
        "constraint": code,
        "message": message,
        "minimum_relaxation": None if amount is None else max(0.0, float(amount)),
        "diagnostic_only": True,
    }


def _prepare(
    cross_section: pd.DataFrame,
    *,
    style_exposures: pd.DataFrame | Mapping[str, Any] | Sequence[str] | None,
    annual_covariance: np.ndarray | pd.DataFrame | None,
    risk_root: np.ndarray | pd.DataFrame | None,
    risk_asset_codes: Sequence[str] | None,
    previous_weights: Mapping[str, float] | pd.Series | None,
    config: StockOptimizerConfig,
) -> _PreparedProblem:
    if not isinstance(cross_section, pd.DataFrame):
        raise ValueError("cross_section_must_be_dataframe")
    required = ["ts_code", "alpha_score", "benchmark_weight", "industry"]
    missing = [column for column in required if column not in cross_section]
    if missing:
        raise ValueError("missing_required_columns:" + ",".join(missing))
    if previous_weights is None:
        raise ValueError("previous_weights_are_required")

    frame = cross_section.copy()
    frame["ts_code"] = frame["ts_code"].astype(str).str.strip()
    frame["industry"] = frame["industry"].fillna("").astype(str).str.strip()
    if (frame["ts_code"] == "").any() or frame["ts_code"].duplicated().any():
        raise ValueError("ts_code_must_be_nonempty_and_unique")
    if (frame["industry"] == "").any():
        raise ValueError("industry_must_be_nonempty")
    frame["alpha_score"] = pd.to_numeric(frame["alpha_score"], errors="coerce")
    frame["benchmark_weight"] = pd.to_numeric(frame["benchmark_weight"], errors="coerce")
    if frame[["alpha_score", "benchmark_weight"]].isna().any().any():
        raise ValueError("alpha_or_benchmark_contains_missing_values")
    if not np.isfinite(frame[["alpha_score", "benchmark_weight"]].to_numpy(dtype=float)).all():
        raise ValueError("alpha_or_benchmark_contains_nonfinite_values")
    if (frame["benchmark_weight"] < -1.0e-12).any():
        raise ValueError("benchmark_weight_must_be_nonnegative")
    input_codes = frame["ts_code"].tolist()
    frame = frame.sort_values("ts_code", kind="mergesort").reset_index(drop=True)
    codes = frame["ts_code"].tolist()
    n = len(codes)
    if n < int(config.target_holdings):
        raise ValueError("cross_section_smaller_than_target_holdings")

    benchmark_raw = frame["benchmark_weight"].to_numpy(dtype=float)
    benchmark, benchmark_sum, benchmark_unit = _normalise_benchmark_weights(
        benchmark_raw, config
    )
    raw_alpha = frame["alpha_score"].to_numpy(dtype=float)
    alpha = _normalise_alpha(
        frame["alpha_score"], benchmark, config.alpha_scale
    )
    styles = _strict_style_frame(frame, style_exposures)
    style_bounds = _resolved_style_bounds(styles, config)
    industry_limits = _industry_limits(frame["industry"].tolist(), config.industry_deviation)
    root, risk_diagnostics = _aligned_risk_root_from_inputs(
        input_codes, codes, annual_covariance, risk_root, risk_asset_codes
    )

    previous_map = {str(key): _finite_float(value, "previous_weight") for key, value in dict(previous_weights).items()}
    if any(value < -1.0e-12 for value in previous_map.values()):
        raise ValueError("previous_weight_must_be_nonnegative")
    previous_total = float(sum(previous_map.values()))
    if previous_total > 1.0 + 1.0e-6:
        raise ValueError("previous_weight_sum_exceeds_one")
    current_set = set(codes)
    outside_positions = {
        code: float(value)
        for code, value in previous_map.items()
        if code not in current_set and float(value) > 0.0
    }
    outside_previous = float(sum(outside_positions.values()))
    cash_previous = max(0.0, 1.0 - previous_total)
    previous = np.asarray([previous_map.get(code, 0.0) for code in codes], dtype=float)

    min_weight = _finite_float(config.min_weight, "min_weight")
    max_weight = _finite_float(config.max_weight, "max_weight")
    max_active = _finite_float(config.max_active_weight, "max_active_weight")
    if min_weight <= 0.0:
        raise ValueError("min_weight_must_be_strictly_positive_for_exact_cardinality")
    if max_weight < min_weight:
        raise ValueError("max_weight_below_min_weight")
    if max_weight > 1.0:
        raise ValueError("max_weight_must_not_exceed_one")
    if max_active < 0.0:
        raise ValueError("max_active_weight_must_be_nonnegative")
    if _finite_float(config.target_tracking_error, "target_tracking_error") < 0.0:
        raise ValueError("target_tracking_error_must_be_nonnegative")
    if _finite_float(config.one_way_turnover_limit, "one_way_turnover_limit") < 0.0:
        raise ValueError("one_way_turnover_limit_must_be_nonnegative")
    for label in (
        "alpha_weight", "alpha_scale", "active_risk_penalty",
        "transaction_cost_rate", "turnover_l1_penalty", "turnover_l2_penalty",
    ):
        if _finite_float(getattr(config, label), label) < 0.0:
            raise ValueError(f"{label}_must_be_nonnegative")

    buy_limits = _limits_by_code(config.buy_limit, codes, "buy_limit")
    sell_limits = _limits_by_code(config.sell_limit, codes, "sell_limit")
    candidate_codes, selection = _candidate_support(
        frame, benchmark, industry_limits, config,
        previous, buy_limits, sell_limits, styles,
    )
    code_position = {code: index for index, code in enumerate(codes)}
    candidate_index = np.asarray(
        [code_position[code] for code in candidate_codes], dtype=int
    )
    candidate_set = set(candidate_codes)
    noncandidate_index = np.asarray(
        [index for index, code in enumerate(codes) if code not in candidate_set],
        dtype=int,
    )
    lower = np.maximum.reduce([
        np.full(n, min_weight),
        benchmark - max_active,
        previous - sell_limits,
        np.zeros(n),
    ])
    upper = np.minimum.reduce([
        np.full(n, max_weight),
        benchmark + max_active,
        previous + buy_limits,
        np.ones(n),
    ])
    active_weight_lower = np.maximum(benchmark - max_active, 0.0)
    active_weight_upper = np.minimum(benchmark + max_active, 1.0)
    execution_exceptions: list[dict[str, Any]] = []
    strategic_upper = np.minimum.reduce([
        np.full(n, max_weight),
        active_weight_upper,
        np.ones(n),
    ])
    forced_retention = (
        previous - sell_limits > strategic_upper + 1.0e-12
    )
    if np.any(forced_retention):
        if not config.allow_forced_retention_execution_exception:
            pass
        else:
            for index in np.flatnonzero(forced_retention):
                effective_weight = float(previous[index] - sell_limits[index])
                upper[index] = effective_weight
                active_weight_upper[index] = effective_weight
                execution_exceptions.append({
                    "ts_code": codes[index],
                    "type": "forced_retention_above_strategic_security_cap",
                    "previous_weight": float(previous[index]),
                    "maximum_sell_weight": float(sell_limits[index]),
                    "minimum_reachable_weight": effective_weight,
                    "strategic_maximum_weight": float(strategic_upper[index]),
                    "temporary_excess_weight": float(
                        effective_weight - strategic_upper[index]
                    ),
                    "target_policy": "maximum_feasible_sell_no_buy",
                    "buy_authority_expanded": False,
                    "other_constraints_relaxed": False,
                })

    # Only support-independent arithmetic may block before the joint MILP.
    # Diagnostics tied to the alpha-first support remain visible but cannot
    # terminate search before HiGHS has optimized the binary support.
    global_hints: list[dict[str, Any]] = []
    initial_support_hints: list[dict[str, Any]] = []
    k = len(candidate_index)
    if k * min_weight > 1.0 + 1.0e-12:
        global_hints.append(_relaxation_hint(
            "minimum_weight",
            "exact_cardinality_minimum_weights_exceed_budget",
            min_weight - 1.0 / k,
        ))
    if k * max_weight < 1.0 - 1.0e-12:
        global_hints.append(_relaxation_hint(
            "maximum_weight",
            "exact_cardinality_maximum_weights_below_budget",
            1.0 / k - max_weight,
        ))
    invalid_bounds = [
        codes[index] for index in candidate_index
        if lower[index] > upper[index] + 1.0e-12
    ]
    if invalid_bounds:
        gap = max(float(lower[code_position[code]] - upper[code_position[code]]) for code in invalid_bounds)
        initial_support_hints.append(_relaxation_hint(
            "candidate_weight_bounds",
            "candidate_weight_bounds_conflict:" + ",".join(invalid_bounds[:12]),
            gap,
        ))
    if len(noncandidate_index):
        noncandidate_active = benchmark[noncandidate_index]
        violation = float(np.max(noncandidate_active - max_active))
        if violation > 1.0e-12:
            initial_support_hints.append(_relaxation_hint(
                "max_active_weight",
                "noncandidate_zero_violates_max_active_weight",
                violation,
            ))
        sell_violation = previous[noncandidate_index] - sell_limits[noncandidate_index]
        if np.isfinite(sell_violation).any() and float(np.nanmax(sell_violation)) > 1.0e-12:
            initial_support_hints.append(_relaxation_hint(
                "sell_limit",
                "noncandidate_cannot_liquidate_within_sell_limit",
                float(np.nanmax(sell_violation)),
            ))
    lower_sum = float(lower[candidate_index].sum())
    upper_sum = float(upper[candidate_index].sum())
    if lower_sum > 1.0 + 1.0e-12:
        initial_support_hints.append(_relaxation_hint("aggregate_lower_bound", "candidate_lower_bounds_exceed_budget", lower_sum - 1.0))
    if upper_sum < 1.0 - 1.0e-12:
        initial_support_hints.append(_relaxation_hint("aggregate_upper_bound", "candidate_upper_bounds_below_budget", 1.0 - upper_sum))

    industry_array = frame["industry"].astype(str).to_numpy()
    for industry, deviation in industry_limits.items():
        mask = industry_array == industry
        candidate_mask = mask.copy()
        candidate_mask[noncandidate_index] = False
        possible_lower = float(lower[candidate_mask].sum())
        possible_upper = float(upper[candidate_mask].sum())
        target = float(benchmark[mask].sum())
        required_lower = max(0.0, target - deviation)
        required_upper = min(1.0, target + deviation)
        if possible_upper < required_lower - 1.0e-12:
            initial_support_hints.append(_relaxation_hint(
                f"industry:{industry}",
                f"industry_candidate_upper_below_lower:{industry}",
                required_lower - possible_upper,
            ))
        if possible_lower > required_upper + 1.0e-12:
            initial_support_hints.append(_relaxation_hint(
                f"industry:{industry}",
                f"industry_candidate_lower_above_upper:{industry}",
                possible_lower - required_upper,
            ))

    distance = np.zeros(n, dtype=float)
    distance[candidate_index] = np.where(
        previous[candidate_index] < lower[candidate_index],
        lower[candidate_index] - previous[candidate_index],
        np.where(
            previous[candidate_index] > upper[candidate_index],
            previous[candidate_index] - upper[candidate_index],
            0.0,
        ),
    )
    distance[noncandidate_index] = previous[noncandidate_index]
    minimum_turnover = 0.5 * (
        float(distance.sum()) + outside_previous + cash_previous
    )
    turnover_limit = float(config.one_way_turnover_limit)
    if minimum_turnover > turnover_limit + 1.0e-12:
        initial_support_hints.append(_relaxation_hint(
            "one_way_turnover_limit",
            "fixed_support_minimum_turnover_exceeds_limit",
            minimum_turnover - turnover_limit,
        ))

    input_hash = _sha256_parts(
        codes, raw_alpha, benchmark, industry_array.tolist(),
        styles.columns.tolist(), styles.to_numpy(dtype=float), previous,
        outside_previous, cash_previous, root, asdict(config), candidate_codes,
    )
    requested = {
        "engine_version": ENGINE_VERSION,
        "selection_method": SELECTION_METHOD,
        "optimizer_form": OPTIMIZER_FORM,
        "is_mixed_integer": True,
        "phase_i_solver": "SCIPY_HIGHS_MILP",
        "phase_i_tracking_error_modeled": False,
        "certified_solver": "CLARABEL",
        "milp_trial_weights_tradable": False,
        "universe_count": n,
        "benchmark_weight_input_sum": benchmark_sum,
        "benchmark_weight_detected_unit": benchmark_unit,
        "benchmark_weight_normalized_sum": float(benchmark.sum()),
        "previous_weight_sum": previous_total,
        "outside_previous_weight": outside_previous,
        "cash_previous_weight": cash_previous,
        "style_names": list(styles.columns),
        "style_bounds": style_bounds,
        "industry_limits": industry_limits,
        "config": _canonical(asdict(config)),
        "selection": selection,
        "execution_exceptions": execution_exceptions,
        "execution_exception_policy": {
            "enabled": bool(
                config.allow_forced_retention_execution_exception
            ),
            "scope": "existing_positions_unreachable_due_to_sell_capacity",
            "target": "minimum_reachable_weight_after_maximum_feasible_sell",
            "buy_authority_expanded": False,
            "other_constraints_relaxed": False,
        },
        "input_hash": input_hash,
    }
    precheck = {
        "status": "passed" if not global_hints else "blocked",
        "scope": "support_independent_before_highs_milp",
        "candidate_count": k,
        "noncandidate_count": int(len(noncandidate_index)),
        "candidate_lower_sum": lower_sum,
        "candidate_upper_sum": upper_sum,
        "coordinatewise_one_way_turnover_lower_bound": minimum_turnover,
        "turnover_lower_bound_is_exact_joint_minimum": False,
        "checks": {
            "exact_candidate_count": k == int(config.target_holdings),
            "strict_positive_minimum_weight": min_weight > 0.0,
            "cardinality_weight_bounds": (
                k * min_weight <= 1.0 + 1.0e-12
                and k * max_weight >= 1.0 - 1.0e-12
            ),
            "initial_support_aggregate_bounds": (
                lower_sum <= 1.0 + 1.0e-12
                and upper_sum >= 1.0 - 1.0e-12
            ),
            "noncandidate_weights_fixed_zero_after_support_selection": True,
            "full_universe_active_vector": True,
            "risk_root_available": True,
        },
        "initial_support_diagnostics": {
            "status": "passed" if not initial_support_hints else "infeasible",
            "blocking_before_joint_milp": False,
            "minimum_relaxation": initial_support_hints,
        },
        "minimum_relaxation": global_hints,
    }
    return _PreparedProblem(
        frame=frame,
        codes=codes,
        industries=industry_array,
        alpha=alpha,
        raw_alpha=raw_alpha,
        benchmark=benchmark,
        previous=previous,
        outside_previous_weight=outside_previous,
        outside_previous_positions=outside_positions,
        cash_previous_weight=cash_previous,
        styles=styles,
        risk_root=root,
        risk_diagnostics=risk_diagnostics,
        candidate_codes=candidate_codes,
        candidate_index=candidate_index,
        noncandidate_index=noncandidate_index,
        lower=lower,
        upper=upper,
        active_weight_lower=active_weight_lower,
        active_weight_upper=active_weight_upper,
        execution_exceptions=tuple(execution_exceptions),
        buy_limits=buy_limits,
        sell_limits=sell_limits,
        industry_limits=industry_limits,
        style_bounds=style_bounds,
        config=config,
        requested=requested,
        input_hash=input_hash,
        precheck=precheck,
    )


def _blocked(
    stage: str,
    reason: str,
    *,
    requested: dict[str, Any] | None = None,
    precheck: dict[str, Any] | None = None,
    relaxation: dict[str, Any] | list[dict[str, Any]] | None = None,
    solver: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "status": "blocked",
        "tradable": False,
        "blocked_stage": stage,
        "reason": reason,
        "requested": requested or {},
        "precheck": precheck or {"status": "blocked"},
        "minimum_relaxation": relaxation or [],
        "solver": solver or {},
        "weights": {},
        "active_weights": {},
        "transactions": [],
        "realized": {},
        "slack": {},
        "dual": {},
        "fallback_used": False,
        "fallback_policy": "forbidden",
    }


def precheck_stock_problem(
    cross_section: pd.DataFrame,
    *,
    style_exposures: pd.DataFrame | Mapping[str, Any] | Sequence[str] | None = None,
    annual_covariance: np.ndarray | pd.DataFrame | None = None,
    risk_root: np.ndarray | pd.DataFrame | None = None,
    risk_asset_codes: Sequence[str] | None = None,
    previous_weights: Mapping[str, float] | pd.Series | None = None,
    config: StockOptimizerConfig | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic data, support, and arithmetic feasibility checks."""

    raw_config = config
    try:
        config = _coerce_config(config)
        prepared = _prepare(
            cross_section,
            style_exposures=style_exposures,
            annual_covariance=annual_covariance,
            risk_root=risk_root,
            risk_asset_codes=risk_asset_codes,
            previous_weights=previous_weights,
            config=config,
        )
        return {
            "status": prepared.precheck["status"],
            "requested": prepared.requested,
            "precheck": prepared.precheck,
            "candidate_codes": prepared.candidate_codes,
            "input_hash": prepared.input_hash,
        }
    except (ValueError, TypeError) as exc:
        return {
            "status": "blocked",
            "reason": str(exc),
            "requested": {"config": _config_payload(raw_config)},
            "precheck": {
                "status": "blocked",
                "minimum_relaxation": [
                    _relaxation_hint("input_contract", str(exc), None)
                ],
            },
            "candidate_codes": [],
        }


def _turnover_expression(prepared: _PreparedProblem, trade: Any) -> Any:
    constant = prepared.outside_previous_weight + prepared.cash_previous_weight
    return 0.5 * (cp.norm1(trade) + constant)


def _build_problem(prepared: _PreparedProblem) -> tuple[Any, Any, dict[str, Any], Any]:
    n = len(prepared.codes)
    w = cp.Variable(n, name="stock_weights")
    active = w - prepared.benchmark
    trade = w - prepared.previous
    constraints: list[Any] = []
    named: dict[str, Any] = {}

    def add(name: str, constraint: Any) -> None:
        constraints.append(constraint)
        named[name] = constraint

    add("budget", cp.sum(w) == 1.0)
    add("candidate_minimum", w[prepared.candidate_index] >= prepared.lower[prepared.candidate_index])
    add("candidate_maximum", w[prepared.candidate_index] <= prepared.upper[prepared.candidate_index])
    if len(prepared.noncandidate_index):
        add("noncandidate_zero", w[prepared.noncandidate_index] == 0.0)
    add("active_upper", w <= prepared.active_weight_upper)
    add("active_lower", w >= prepared.active_weight_lower)

    for industry, deviation in prepared.industry_limits.items():
        indexes = np.flatnonzero(prepared.industries == industry)
        exposure = cp.sum(active[indexes])
        add(f"industry_upper:{industry}", exposure <= deviation)
        add(f"industry_lower:{industry}", exposure >= -deviation)

    style_matrix = prepared.styles.to_numpy(dtype=float)
    for style_index, style in enumerate(prepared.styles.columns):
        exposure = style_matrix[:, style_index] @ active
        lower, upper = prepared.style_bounds[style]
        add(f"style_upper:{style}", exposure <= upper)
        add(f"style_lower:{style}", exposure >= lower)

    tracking_error = cp.norm(prepared.risk_root @ active, 2)
    add("tracking_error", tracking_error <= prepared.config.target_tracking_error)
    turnover = _turnover_expression(prepared, trade)
    add("one_way_turnover", turnover <= prepared.config.one_way_turnover_limit)
    if np.isfinite(prepared.buy_limits).any():
        finite = np.flatnonzero(np.isfinite(prepared.buy_limits))
        add("buy_limits", trade[finite] <= prepared.buy_limits[finite])
    if np.isfinite(prepared.sell_limits).any():
        finite = np.flatnonzero(np.isfinite(prepared.sell_limits))
        add("sell_limits", -trade[finite] <= prepared.sell_limits[finite])

    score_target = _support_score_target(prepared)
    objective = cp.Maximize(
        prepared.config.alpha_weight * (prepared.alpha @ w)
        - prepared.config.score_target_penalty
        * cp.sum_squares(w - score_target)
        - prepared.config.active_risk_penalty
        * cp.sum_squares(prepared.risk_root @ active)
        - (prepared.config.transaction_cost_rate + prepared.config.turnover_l1_penalty)
        * turnover
        - prepared.config.turnover_l2_penalty * cp.sum_squares(trade)
    )
    return cp.Problem(objective, constraints), w, named, turnover


def _dual_payload(constraint: Any, labels: Sequence[str] | None = None) -> Any:
    if constraint.dual_value is None:
        return None
    value = np.asarray(constraint.dual_value, dtype=float)
    if value.ndim == 0:
        return float(value)
    flat = value.reshape(-1)
    if labels is not None and len(labels) == len(flat):
        return {str(label): float(item) for label, item in zip(labels, flat)}
    return {
        "count": int(len(flat)),
        "minimum": float(flat.min()) if len(flat) else 0.0,
        "maximum": float(flat.max()) if len(flat) else 0.0,
        "l1": float(np.abs(flat).sum()),
        "nonzero": int(np.sum(np.abs(flat) > 1.0e-9)),
    }


def _solution_diagnostics(prepared: _PreparedProblem, weights: np.ndarray) -> dict[str, Any]:
    active = weights - prepared.benchmark
    trade = weights - prepared.previous
    turnover = 0.5 * (
        float(np.abs(trade).sum())
        + prepared.outside_previous_weight
        + prepared.cash_previous_weight
    )
    tracking_error = float(np.linalg.norm(prepared.risk_root @ active))
    score_target = _support_score_target(prepared)
    candidate_weights = weights[prepared.candidate_index]
    noncandidate_weights = weights[prepared.noncandidate_index]
    industry_active = {
        industry: float(active[prepared.industries == industry].sum())
        for industry in prepared.industry_limits
    }
    style_active = {
        style: float(prepared.styles[style].to_numpy(dtype=float) @ active)
        for style in prepared.styles.columns
    }
    buy = np.maximum(trade, 0.0)
    sell = np.maximum(-trade, 0.0)
    slacks: dict[str, Any] = {
        "budget_residual": abs(float(weights.sum()) - 1.0),
        "candidate_minimum": {
            code: float(weights[index] - prepared.lower[index])
            for code, index in zip(prepared.candidate_codes, prepared.candidate_index)
        },
        "candidate_maximum": {
            code: float(prepared.upper[index] - weights[index])
            for code, index in zip(prepared.candidate_codes, prepared.candidate_index)
        },
        "noncandidate_zero_max_abs": float(np.max(np.abs(noncandidate_weights)))
        if len(noncandidate_weights) else 0.0,
        "max_active_weight": float(min(
            np.min(prepared.active_weight_upper - weights),
            np.min(weights - prepared.active_weight_lower),
        )),
        "execution_exceptions": list(prepared.execution_exceptions),
        "industry": {
            industry: float(prepared.industry_limits[industry] - abs(value))
            for industry, value in industry_active.items()
        },
        "style": {
            style: {
                "lower": float(style_active[style] - prepared.style_bounds[style][0]),
                "upper": float(prepared.style_bounds[style][1] - style_active[style]),
            }
            for style in style_active
        },
        "tracking_error": float(prepared.config.target_tracking_error - tracking_error),
        "one_way_turnover": float(prepared.config.one_way_turnover_limit - turnover),
        "buy_limits": float(np.min(prepared.buy_limits - buy))
        if np.isfinite(prepared.buy_limits).all() else (
            float(np.min((prepared.buy_limits - buy)[np.isfinite(prepared.buy_limits)]))
            if np.isfinite(prepared.buy_limits).any() else None
        ),
        "sell_limits": float(np.min(prepared.sell_limits - sell))
        if np.isfinite(prepared.sell_limits).all() else (
            float(np.min((prepared.sell_limits - sell)[np.isfinite(prepared.sell_limits)]))
            if np.isfinite(prepared.sell_limits).any() else None
        ),
    }
    violations = [
        abs(float(weights.sum()) - 1.0),
        # Candidate minimum violations are measured on the next line.
        max(0.0, float(np.max(prepared.lower[prepared.candidate_index] - candidate_weights))),
        max(0.0, float(np.max(candidate_weights - prepared.upper[prepared.candidate_index]))),
        float(np.max(np.abs(noncandidate_weights))) if len(noncandidate_weights) else 0.0,
        max(0.0, float(np.max(weights - prepared.active_weight_upper))),
        max(0.0, float(np.max(prepared.active_weight_lower - weights))),
        max((max(0.0, abs(value) - prepared.industry_limits[industry]) for industry, value in industry_active.items()), default=0.0),
        max((max(0.0, prepared.style_bounds[name][0] - value, value - prepared.style_bounds[name][1]) for name, value in style_active.items()), default=0.0),
        max(0.0, tracking_error - prepared.config.target_tracking_error),
        max(0.0, turnover - prepared.config.one_way_turnover_limit),
    ]
    if np.isfinite(prepared.buy_limits).any():
        finite = np.isfinite(prepared.buy_limits)
        violations.append(max(0.0, float(np.max(buy[finite] - prepared.buy_limits[finite]))))
    if np.isfinite(prepared.sell_limits).any():
        finite = np.isfinite(prepared.sell_limits)
        violations.append(max(0.0, float(np.max(sell[finite] - prepared.sell_limits[finite]))))
    holdings = int(np.sum(weights > prepared.config.min_weight / 2.0))
    if holdings != prepared.config.target_holdings:
        violations.append(1.0)
    return {
        "active": active,
        "trade": trade,
        "turnover": turnover,
        "tracking_error": tracking_error,
        "score_target_l2_distance": float(
            np.linalg.norm(weights - score_target)
        ),
        "score_target": score_target,
        "industry_active": industry_active,
        "style_active": style_active,
        "buy": buy,
        "sell": sell,
        "slack": slacks,
        "holdings_count": holdings,
        "max_constraint_violation": float(max(violations, default=0.0)),
    }


def _minimum_relaxation_socp(prepared: _PreparedProblem) -> dict[str, Any]:
    """Find diagnostic-only aggregate slacks; never return the trial weights."""

    if cp is None or "CLARABEL" not in cp.installed_solvers():
        return {
            "status": "unavailable",
            "solver": "CLARABEL",
            "diagnostic_only": True,
            "weights_returned": False,
        }
    n = len(prepared.codes)
    w = cp.Variable(n)
    active = w - prepared.benchmark
    trade = w - prepared.previous
    s_budget_pos = cp.Variable(nonneg=True)
    s_budget_neg = cp.Variable(nonneg=True)
    s_min = cp.Variable(nonneg=True)
    s_max = cp.Variable(nonneg=True)
    s_active = cp.Variable(nonneg=True)
    s_te = cp.Variable(nonneg=True)
    s_turnover = cp.Variable(nonneg=True)
    constraints = [
        cp.sum(w) - 1.0 <= s_budget_pos,
        1.0 - cp.sum(w) <= s_budget_neg,
        w[prepared.candidate_index] >= prepared.lower[prepared.candidate_index] - s_min,
        w[prepared.candidate_index] <= prepared.upper[prepared.candidate_index] + s_max,
        w[prepared.candidate_index] >= 0.0,
        w <= prepared.active_weight_upper + s_active,
        w >= prepared.active_weight_lower - s_active,
        cp.norm(prepared.risk_root @ active, 2)
        <= prepared.config.target_tracking_error + s_te,
        _turnover_expression(prepared, trade)
        <= prepared.config.one_way_turnover_limit + s_turnover,
    ]
    if len(prepared.noncandidate_index):
        constraints.append(w[prepared.noncandidate_index] == 0.0)
    industry_slacks: dict[str, Any] = {}
    for industry, limit in prepared.industry_limits.items():
        slack = cp.Variable(nonneg=True)
        industry_slacks[industry] = slack
        indexes = np.flatnonzero(prepared.industries == industry)
        exposure = cp.sum(active[indexes])
        constraints.extend([exposure <= limit + slack, exposure >= -limit - slack])
    style_slacks: dict[str, Any] = {}
    style_matrix = prepared.styles.to_numpy(dtype=float)
    for index, style in enumerate(prepared.styles.columns):
        lower, upper = prepared.style_bounds[style]
        slack = cp.Variable(nonneg=True)
        style_slacks[style] = slack
        exposure = style_matrix[:, index] @ active
        constraints.extend([exposure <= upper + slack, exposure >= lower - slack])
    s_buy = cp.Variable(nonneg=True)
    s_sell = cp.Variable(nonneg=True)
    if np.isfinite(prepared.buy_limits).any():
        finite = np.flatnonzero(np.isfinite(prepared.buy_limits))
        constraints.append(trade[finite] <= prepared.buy_limits[finite] + s_buy)
    else:
        constraints.append(s_buy == 0.0)
    if np.isfinite(prepared.sell_limits).any():
        finite = np.flatnonzero(np.isfinite(prepared.sell_limits))
        constraints.append(-trade[finite] <= prepared.sell_limits[finite] + s_sell)
    else:
        constraints.append(s_sell == 0.0)
    aggregate_slack = 100.0 * (s_budget_pos + s_budget_neg) + 10.0 * (s_min + s_max + s_active + s_te + s_turnover + s_buy + s_sell)
    if industry_slacks:
        aggregate_slack += 10.0 * cp.sum(cp.hstack(list(industry_slacks.values())))
    if style_slacks:
        aggregate_slack += 10.0 * cp.sum(cp.hstack(list(style_slacks.values())))
    objective = cp.Minimize(aggregate_slack)
    problem = cp.Problem(objective, constraints)
    started = time.perf_counter()
    try:
        problem.solve(
            solver="CLARABEL",
            verbose=False,
            max_iter=int(prepared.config.solver_max_iterations),
            tol_gap_abs=1.0e-8,
            tol_gap_rel=1.0e-8,
            tol_feas=1.0e-8,
        )
    except Exception as exc:  # pragma: no cover - solver-runtime specific.
        return {
            "status": "failed",
            "solver": "CLARABEL",
            "reason": f"{type(exc).__name__}:{exc}",
            "diagnostic_only": True,
            "weights_returned": False,
        }
    values = {
        "budget_positive": float(s_budget_pos.value or 0.0),
        "budget_negative": float(s_budget_neg.value or 0.0),
        "minimum_weight": float(s_min.value or 0.0),
        "maximum_weight": float(s_max.value or 0.0),
        "max_active_weight": float(s_active.value or 0.0),
        "tracking_error": float(s_te.value or 0.0),
        "one_way_turnover": float(s_turnover.value or 0.0),
        "buy_limit": float(s_buy.value or 0.0),
        "sell_limit": float(s_sell.value or 0.0),
        "industry": {key: float(value.value or 0.0) for key, value in industry_slacks.items()},
        "style": {key: float(value.value or 0.0) for key, value in style_slacks.items()},
    }
    return {
        "status": str(problem.status),
        "solver": "CLARABEL",
        "relaxation_semantics": "weighted_joint_relaxation_not_individual_minima",
        "constraint_specific_minima_computed": False,
        "solve_time_ms": (time.perf_counter() - started) * 1000.0,
        "relaxations": values,
        "diagnostic_only": True,
        "weights_returned": False,
    }


def _phase_i_support_feasibility(prepared: _PreparedProblem) -> dict[str, Any]:
    """Test one frozen support with the original constraints and no trial output."""

    problem, variable, _, _ = _build_problem(prepared)
    feasibility = cp.Problem(cp.Minimize(0.0), problem.constraints)
    started = time.perf_counter()
    try:
        feasibility.solve(
            solver="CLARABEL",
            verbose=False,
            max_iter=int(prepared.config.solver_max_iterations),
            tol_gap_abs=1.0e-9,
            tol_gap_rel=1.0e-9,
            tol_feas=1.0e-9,
        )
    except Exception as exc:  # pragma: no cover - solver-runtime specific.
        return {
            "status": "exception",
            "reason": f"clarabel_exception:{type(exc).__name__}:{exc}",
            "solve_time_ms": (time.perf_counter() - started) * 1000.0,
            "trial_weights_returned": False,
        }
    status = str(feasibility.status)
    if status in {"optimal", "optimal_inaccurate"} and variable.value is not None:
        raw_weights = np.asarray(variable.value, dtype=float).reshape(-1)
        if raw_weights.shape == (len(prepared.codes),) and np.isfinite(raw_weights).all():
            weights = raw_weights.copy()
            weights[prepared.noncandidate_index] = 0.0
            diagnostics = _solution_diagnostics(prepared, weights)
            tolerance = float(prepared.config.feasibility_tolerance)
            if diagnostics["max_constraint_violation"] <= tolerance:
                return {
                    "status": "feasible",
                    "reason": f"clarabel_status:{status}:independent_residual_certified",
                    "solve_time_ms": (time.perf_counter() - started) * 1000.0,
                    "max_constraint_violation": diagnostics["max_constraint_violation"],
                    "trial_weights_returned": False,
                }
    return {
        "status": "feasible" if status == "optimal" else "infeasible",
        "reason": f"clarabel_status:{status}",
        "solve_time_ms": (time.perf_counter() - started) * 1000.0,
        "trial_weights_returned": False,
    }


def _prepared_on_support(
    prepared: _PreparedProblem,
    candidate_codes: Sequence[str],
) -> _PreparedProblem:
    """Return an equivalent full-universe problem with a different exact support."""

    positions = {code: index for index, code in enumerate(prepared.codes)}
    candidate_set = set(map(str, candidate_codes))
    if len(candidate_set) != int(prepared.config.target_holdings):
        raise ValueError("support_search_candidate_count_mismatch")
    if not candidate_set.issubset(positions):
        raise ValueError("support_search_candidate_outside_universe")
    ordered = sorted(
        candidate_set,
        key=lambda code: (-float(prepared.raw_alpha[positions[code]]), code),
    )
    candidate_index = np.asarray([positions[code] for code in ordered], dtype=int)
    noncandidate_index = np.asarray(
        [index for index, code in enumerate(prepared.codes) if code not in candidate_set],
        dtype=int,
    )
    selection = dict(prepared.requested["selection"])
    selection.update({
        "candidate_codes": ordered,
        "candidate_count": len(ordered),
        "candidate_hash": _sha256_parts(ordered),
    })
    input_hash = _sha256_parts(prepared.input_hash, ordered)
    requested = dict(prepared.requested)
    requested["selection"] = selection
    requested["input_hash"] = input_hash
    precheck = dict(prepared.precheck)
    precheck.update({
        "candidate_count": len(ordered),
        "noncandidate_count": int(len(noncandidate_index)),
        "candidate_lower_sum": float(prepared.lower[candidate_index].sum()),
        "candidate_upper_sum": float(prepared.upper[candidate_index].sum()),
        "support_recheck": "highs_linear_feasible_then_clarabel_socp_required",
    })
    return replace(
        prepared,
        candidate_codes=ordered,
        candidate_index=candidate_index,
        noncandidate_index=noncandidate_index,
        requested=requested,
        input_hash=input_hash,
        precheck=precheck,
    )


def _highs_milp_available() -> bool:
    return all(
        item is not None
        for item in (scipy_milp, ScipyBounds, ScipyLinearConstraint, coo_matrix)
    )


def _optional_solver_number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def _milp_linear_diagnostics(
    prepared: _PreparedProblem,
    weights: np.ndarray,
    support_binary: np.ndarray,
    absolute_trade: np.ndarray,
    excluded_supports: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Independently recompute every Phase-I linear residual."""

    active = weights - prepared.benchmark
    trade = weights - prepared.previous
    selected = support_binary >= 0.5
    eligible = set(map(str, prepared.requested["selection"].get("eligible_codes", ())))
    required = set(map(str, prepared.requested["selection"].get("required_codes", ())))
    selected_codes = {
        code for code, included in zip(prepared.codes, selected) if bool(included)
    }
    true_turnover = 0.5 * (
        float(np.abs(trade).sum())
        + prepared.outside_previous_weight
        + prepared.cash_previous_weight
    )
    industry_violations = [
        max(
            0.0,
            abs(float(active[prepared.industries == industry].sum())) - limit,
        )
        for industry, limit in prepared.industry_limits.items()
    ]
    style_matrix = prepared.styles.to_numpy(dtype=float)
    style_violations: list[float] = []
    for style_index, style in enumerate(prepared.styles.columns):
        exposure = float(style_matrix[:, style_index] @ active)
        lower, upper = prepared.style_bounds[style]
        style_violations.append(max(0.0, lower - exposure, exposure - upper))
    violations = [
        abs(float(weights.sum()) - 1.0),
        abs(float(support_binary.sum()) - float(prepared.config.target_holdings)),
        float(np.max(np.abs(support_binary - np.round(support_binary)))),
        max(0.0, float(np.max(prepared.lower * support_binary - weights))),
        max(0.0, float(np.max(weights - prepared.upper * support_binary))),
        max(0.0, float(np.max(weights - prepared.active_weight_upper))),
        max(0.0, float(np.max(prepared.active_weight_lower - weights))),
        max(industry_violations, default=0.0),
        max(style_violations, default=0.0),
        max(0.0, true_turnover - prepared.config.one_way_turnover_limit),
        max(0.0, float(np.max(trade - absolute_trade))),
        max(0.0, float(np.max(-trade - absolute_trade))),
        max((float(support_binary[index]) for index, code in enumerate(prepared.codes) if code not in eligible), default=0.0),
        max((1.0 - float(support_binary[index]) for index, code in enumerate(prepared.codes) if code in required), default=0.0),
    ]
    if np.isfinite(prepared.buy_limits).any():
        finite = np.isfinite(prepared.buy_limits)
        violations.append(
            max(0.0, float(np.max(trade[finite] - prepared.buy_limits[finite])))
        )
    if np.isfinite(prepared.sell_limits).any():
        finite = np.isfinite(prepared.sell_limits)
        violations.append(
            max(0.0, float(np.max(-trade[finite] - prepared.sell_limits[finite])))
        )
    excluded = {tuple(sorted(map(str, support))) for support in excluded_supports}
    if tuple(sorted(selected_codes)) in excluded:
        violations.append(1.0)
    if len(selected_codes) != int(prepared.config.target_holdings):
        violations.append(1.0)
    return {
        "max_constraint_violation": float(max(violations, default=0.0)),
        "support_count": len(selected_codes),
        "binary_integrality_violation": float(
            np.max(np.abs(support_binary - np.round(support_binary)))
        ),
        "one_way_turnover": true_turnover,
        "eligible_support_only": selected_codes.issubset(eligible),
        "required_codes_included": required.issubset(selected_codes),
        "trial_weights_returned": False,
    }


def _solve_highs_linear_support(
    prepared: _PreparedProblem,
    excluded_supports: Sequence[Sequence[str]],
) -> dict[str, Any]:
    """Solve exact-cardinality support and all non-TE constraints jointly."""

    if not _highs_milp_available():
        return {
            "status": "unavailable",
            "reason": "scipy_highs_milp_unavailable",
            "solver": "SCIPY_HIGHS_MILP",
            "support_codes": None,
            "trial_weights_returned": False,
        }

    n = len(prepared.codes)
    support_offset = n
    trade_offset = 2 * n
    variable_count = 3 * n
    objective = np.zeros(variable_count, dtype=float)
    alpha_rank = pd.Series(prepared.raw_alpha).rank(
        method="average", pct=True
    ).to_numpy(dtype=float)
    benchmark_rank = pd.Series(prepared.benchmark).rank(
        method="average", pct=True
    ).to_numpy(dtype=float)
    asset_risk = np.linalg.norm(prepared.risk_root, axis=0)
    low_risk_rank = 1.0 - pd.Series(asset_risk).rank(
        method="average", pct=True
    ).to_numpy(dtype=float)
    support_rank = (
        0.80 * alpha_rank
        + 0.10 * benchmark_rank
        + 0.00 * low_risk_rank
        + 0.10 * (prepared.previous > prepared.config.min_weight / 2.0).astype(float)
    )
    deterministic_tie_break = (
        np.arange(n, 0, -1, dtype=float) / max(float(n), 1.0)
    )
    # Phase I is the exact support-selection implementation of the user's
    # score-to-portfolio mandate.  Every linear mandate remains hard, while the
    # support objective is made TE-aware in an explicitly audited way so that
    # Clarabel is not forced to reject many alpha-only supports ex post.
    objective[:n] = -support_rank
    objective[support_offset:trade_offset] = (
        -1.0e-6 * support_rank - 1.0e-10 * deterministic_tie_break
    )
    objective[trade_offset:] = 0.5 * (
        prepared.config.transaction_cost_rate
        + prepared.config.turnover_l1_penalty
    )

    variable_lower = np.zeros(variable_count, dtype=float)
    variable_upper = np.full(variable_count, np.inf, dtype=float)
    variable_upper[:n] = 1.0
    variable_upper[support_offset:trade_offset] = 1.0
    integrality = np.zeros(variable_count, dtype=np.uint8)
    integrality[support_offset:trade_offset] = 1

    selection = prepared.requested["selection"]
    eligible = set(map(str, selection.get("eligible_codes", ())))
    required = set(map(str, selection.get("required_codes", ())))
    for index, code in enumerate(prepared.codes):
        if code not in eligible:
            variable_upper[support_offset + index] = 0.0
        if code in required:
            variable_lower[support_offset + index] = 1.0
            variable_upper[support_offset + index] = 1.0

    matrix_rows: list[int] = []
    matrix_columns: list[int] = []
    matrix_values: list[float] = []
    constraint_lower: list[float] = []
    constraint_upper: list[float] = []

    def add_row(
        coefficients: Sequence[tuple[int, float]],
        lower: float = -np.inf,
        upper: float = np.inf,
    ) -> None:
        row = len(constraint_lower)
        for column, value in coefficients:
            number = float(value)
            if number != 0.0:
                matrix_rows.append(row)
                matrix_columns.append(int(column))
                matrix_values.append(number)
        constraint_lower.append(float(lower))
        constraint_upper.append(float(upper))

    add_row([(index, 1.0) for index in range(n)], 1.0, 1.0)
    add_row(
        [(support_offset + index, 1.0) for index in range(n)],
        float(prepared.config.target_holdings),
        float(prepared.config.target_holdings),
    )
    for index in range(n):
        # Selected names respect the full per-name lower/upper envelope.
        add_row(
            [(index, 1.0), (support_offset + index, -prepared.lower[index])],
            0.0,
            np.inf,
        )
        add_row(
            [(index, 1.0), (support_offset + index, -prepared.upper[index])],
            -np.inf,
            0.0,
        )
        # These constraints remain unconditional when z=0.  They prevent an
        # unselected name from silently evading active or liquidation bounds.
        add_row(
            [(index, 1.0)],
            prepared.active_weight_lower[index],
            prepared.active_weight_upper[index],
        )
        add_row(
            [(index, 1.0), (trade_offset + index, -1.0)],
            -np.inf,
            prepared.previous[index],
        )
        add_row(
            [(index, -1.0), (trade_offset + index, -1.0)],
            -np.inf,
            -prepared.previous[index],
        )
        if math.isfinite(float(prepared.buy_limits[index])):
            add_row(
                [(index, 1.0)],
                -np.inf,
                prepared.previous[index] + prepared.buy_limits[index],
            )
        if math.isfinite(float(prepared.sell_limits[index])):
            add_row(
                [(index, 1.0)],
                prepared.previous[index] - prepared.sell_limits[index],
                np.inf,
            )

    for industry, deviation in prepared.industry_limits.items():
        indexes = np.flatnonzero(prepared.industries == industry)
        benchmark_weight = float(prepared.benchmark[indexes].sum())
        add_row(
            [(int(index), 1.0) for index in indexes],
            benchmark_weight - deviation,
            benchmark_weight + deviation,
        )

    style_matrix = prepared.styles.to_numpy(dtype=float)
    for style_index, style in enumerate(prepared.styles.columns):
        exposure = style_matrix[:, style_index]
        benchmark_exposure = float(prepared.benchmark @ exposure)
        lower, upper = prepared.style_bounds[style]
        add_row(
            [(index, exposure[index]) for index in range(n)],
            benchmark_exposure + lower,
            benchmark_exposure + upper,
        )

    turnover_capacity = (
        2.0 * prepared.config.one_way_turnover_limit
        - prepared.outside_previous_weight
        - prepared.cash_previous_weight
    )
    add_row(
        [(trade_offset + index, 1.0) for index in range(n)],
        -np.inf,
        turnover_capacity,
    )
    for support in excluded_supports:
        support_set = set(map(str, support))
        indexes = [
            index for index, code in enumerate(prepared.codes) if code in support_set
        ]
        if len(indexes) == int(prepared.config.target_holdings):
            add_row(
                [(support_offset + index, 1.0) for index in indexes],
                -np.inf,
                float(prepared.config.target_holdings - 1),
            )

    matrix = coo_matrix(
        (matrix_values, (matrix_rows, matrix_columns)),
        shape=(len(constraint_lower), variable_count),
        dtype=float,
    ).tocsr()
    constraints = ScipyLinearConstraint(
        matrix,
        np.asarray(constraint_lower, dtype=float),
        np.asarray(constraint_upper, dtype=float),
    )
    bounds = ScipyBounds(variable_lower, variable_upper)
    solver_options: dict[str, Any] = {
        "disp": False,
        "presolve": True,
        "mip_rel_gap": 0.0,
    }
    milp_time_limit = float(prepared.config.milp_time_limit_seconds)
    if milp_time_limit > 0.0:
        solver_options["time_limit"] = milp_time_limit
    started = time.perf_counter()
    try:
        result = scipy_milp(
            objective,
            integrality=integrality,
            bounds=bounds,
            constraints=constraints,
            options=solver_options,
        )
    except Exception as exc:  # pragma: no cover - solver-runtime specific.
        return {
            "status": "exception",
            "reason": f"highs_milp_exception:{type(exc).__name__}:{exc}",
            "solver": "SCIPY_HIGHS_MILP",
            "solve_time_ms": (time.perf_counter() - started) * 1000.0,
            "support_codes": None,
            "trial_weights_returned": False,
        }

    status_code = int(result.status)
    audit: dict[str, Any] = {
        "solver": "SCIPY_HIGHS_MILP",
        "highs_status_code": status_code,
        "highs_success": bool(result.success),
        "highs_message": str(result.message),
        "highs_options": _canonical(solver_options),
        "time_limit_seconds": milp_time_limit,
        "time_limit_enabled": milp_time_limit > 0.0,
        "time_limit_reached_or_iteration_limit": status_code == 1,
        "mip_gap": _optional_solver_number(getattr(result, "mip_gap", None)),
        "mip_node_count": _optional_solver_number(
            getattr(result, "mip_node_count", None)
        ),
        "objective_value": _optional_solver_number(getattr(result, "fun", None)),
        "solve_time_ms": (time.perf_counter() - started) * 1000.0,
        "support_codes": None,
        "trial_weights_returned": False,
        "milp_trial_weights_discarded": True,
        "support_selection_objective": (
            "maximize_alpha_benchmark_coverage_low_risk_rank_net_linear_turnover_cost"
        ),
        "phase_i_support_rank_blend": {
            "alpha_rank": 0.80,
            "benchmark_weight_rank": 0.10,
            "low_asset_risk_rank": 0.00,
            "prior_live_support": 0.10,
        },
        "hidden_benchmark_previous_risk_rank_blend": False,
    }
    if status_code not in {0, 1} or result.x is None:
        audit["status"] = "infeasible" if status_code == 2 else "nonoptimal"
        audit["reason"] = f"highs_status:{status_code}:{result.message}"
        return audit

    solution = np.asarray(result.x, dtype=float)
    if solution.shape != (variable_count,) or not np.isfinite(solution).all():
        audit.update({
            "status": "invalid_solution",
            "reason": "highs_returned_nonfinite_or_wrong_shape_solution",
        })
        return audit
    weights = solution[:n]
    support_binary = solution[support_offset:trade_offset]
    absolute_trade = solution[trade_offset:]
    diagnostics = _milp_linear_diagnostics(
        prepared,
        weights,
        support_binary,
        absolute_trade,
        excluded_supports,
    )
    support_codes = [
        code
        for code, included in zip(prepared.codes, support_binary >= 0.5)
        if bool(included)
    ]
    audit["linear_diagnostics"] = diagnostics
    audit["support_count"] = len(support_codes)
    audit["support_hash"] = (
        _sha256_parts(sorted(support_codes))
        if len(support_codes) == int(prepared.config.target_holdings)
        else None
    )
    tolerance = float(prepared.config.feasibility_tolerance)
    if (
        len(support_codes) != int(prepared.config.target_holdings)
        or diagnostics["max_constraint_violation"] > tolerance
    ):
        audit.update({
            "status": "invalid_solution",
            "reason": "highs_solution_failed_independent_linear_audit",
        })
        return audit
    audit.update({
        "status": "linear_feasible",
        "reason": (
            "highs_optimal_linear_support"
            if status_code == 0 and bool(result.success)
            else "highs_timeboxed_feasible_incumbent_independently_audited"
        ),
        "phase_i_optimality_proven": bool(status_code == 0 and result.success),
        "phase_i_feasible_incumbent_used": bool(
            status_code != 0 or not bool(result.success)
        ),
        "support_codes": support_codes,
    })
    return audit


def _with_support_search_audit(
    prepared: _PreparedProblem,
    attempts: list[dict[str, Any]],
    selected_strategy: str | None,
) -> _PreparedProblem:
    requested = dict(prepared.requested)
    selection = dict(requested["selection"])
    selection["support_search"] = {
        "method": "alpha_candidate_then_scipy_highs_joint_support_then_clarabel_socp",
        "phase_i_linear_solver": "SCIPY_HIGHS_MILP",
        "phase_i_uses_all_linear_mandate_constraints": True,
        "phase_i_tracking_error_modeled": False,
        "initial_alpha_candidate_certifier": "CLARABEL",
        "prior_live_support_used_only_after_alpha_and_milp_search": True,
        "tracking_error_certifier": "CLARABEL",
        "clarabel_uses_original_constraints": True,
        "trial_weights_returned": False,
        "milp_trial_weights_tradable": False,
        "heuristic_support_fallback_used": False,
        "no_good_cut_per_rejected_support": True,
        "milp_attempt_limit": int(prepared.config.support_search_max_attempts),
        "attempt_limit": int(prepared.config.support_search_max_attempts) + 2,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "selected_strategy": selected_strategy,
        "selected_support_hash": (
            selection["candidate_hash"] if selected_strategy is not None else None
        ),
    }
    requested["selection"] = selection
    return replace(prepared, requested=requested)


def _search_feasible_support(
    prepared: _PreparedProblem,
) -> tuple[_PreparedProblem | None, list[dict[str, Any]]]:
    """Find a certified support, preferring current alpha before continuity."""

    attempts: list[dict[str, Any]] = []
    excluded_supports: list[list[str]] = []
    attempt_limit = int(prepared.config.support_search_max_attempts)
    previous_support = [
        code for code, weight in zip(prepared.codes, prepared.previous)
        if float(weight) > float(prepared.config.min_weight) / 2.0
    ]

    def certify_frozen_support(
        support_codes: Sequence[str],
        *,
        ordinal: int,
        strategy: str,
        selected_strategy: str,
        prior_live_support: bool = False,
        initial_alpha_candidate: bool = False,
    ) -> tuple[_PreparedProblem | None, bool]:
        try:
            trial = _prepared_on_support(prepared, support_codes)
            outcome = _phase_i_support_feasibility(trial)
            attempt = {
                "ordinal": ordinal,
                "strategy": strategy,
                "support_hash": trial.requested["selection"]["candidate_hash"],
                "support_count": len(support_codes),
                "clarabel_certification": outcome["status"],
                "clarabel_reason": outcome["reason"],
                "clarabel_solve_time_ms": outcome["solve_time_ms"],
                "status": (
                    "certified" if outcome["status"] == "feasible"
                    else "rejected_by_clarabel_socp"
                ),
                "trial_weights_returned": False,
                "prior_live_support": bool(prior_live_support),
                "initial_alpha_candidate": bool(initial_alpha_candidate),
                "milp_trial_weights_discarded": False,
            }
            attempts.append(attempt)
            if outcome["status"] == "feasible":
                return (
                    _with_support_search_audit(
                        trial, attempts, selected_strategy
                    ),
                    True,
                )
            excluded_supports.append(list(support_codes))
        except ValueError as exc:
            attempts.append({
                "ordinal": ordinal,
                "strategy": strategy,
                "status": "rejected_before_clarabel",
                "reason": str(exc),
                "trial_weights_returned": False,
                "prior_live_support": bool(prior_live_support),
                "initial_alpha_candidate": bool(initial_alpha_candidate),
                "milp_trial_weights_discarded": False,
            })
        return None, False

    certified, accepted = certify_frozen_support(
        prepared.candidate_codes,
        ordinal=-2,
        strategy="initial_alpha_industry_candidate_clarabel_certification",
        selected_strategy="initial_alpha_industry_candidate_clarabel_certified",
        initial_alpha_candidate=True,
    )
    if accepted and certified is not None:
        return certified, attempts

    for ordinal in range(attempt_limit):
        milp = _solve_highs_linear_support(prepared, excluded_supports)
        support = milp.pop("support_codes", None)
        attempt: dict[str, Any] = {
            "ordinal": ordinal,
            "strategy": "highs_milp_joint_linear_support",
            **milp,
        }
        if support is None:
            attempt.update({
                "clarabel_certification": "not_run",
                "trial_weights_returned": False,
            })
            attempts.append(attempt)
            break

        trial = _prepared_on_support(prepared, support)
        outcome = _phase_i_support_feasibility(trial)
        attempt.update({
            "support_hash": trial.requested["selection"]["candidate_hash"],
            "clarabel_certification": outcome["status"],
            "clarabel_reason": outcome["reason"],
            "clarabel_solve_time_ms": outcome["solve_time_ms"],
            "status": (
                "certified" if outcome["status"] == "feasible"
                else "rejected_by_clarabel_socp"
            ),
            "trial_weights_returned": False,
            "milp_trial_weights_discarded": True,
        })
        attempts.append(attempt)
        if outcome["status"] == "feasible":
            return (
                _with_support_search_audit(
                    trial, attempts, "highs_milp_joint_linear_support"
                ),
                attempts,
            )
        excluded_supports.append(list(support))
    if len(previous_support) == int(prepared.config.target_holdings):
        certified, accepted = certify_frozen_support(
            previous_support,
            ordinal=attempt_limit,
            strategy="prior_live_support_clarabel_certification",
            selected_strategy="prior_live_support_clarabel_certified_after_alpha_search",
            prior_live_support=True,
        )
        if accepted and certified is not None:
            return certified, attempts
    return None, attempts

def top_score_long_only_baseline(
    prepared: _PreparedProblem,
) -> dict[str, Any]:
    """Top-K score-proportional long-only baseline with identical score/cost units."""

    blacklist = set(map(str, prepared.config.blacklist))
    whitelist = set(map(str, prepared.config.whitelist))
    eligible = [
        code for code in prepared.codes
        if code not in blacklist and (not whitelist or code in whitelist)
    ]
    raw = dict(zip(prepared.codes, prepared.raw_alpha))
    selected = sorted(eligible, key=lambda code: (-raw[code], code))[
        : prepared.config.target_holdings
    ]
    position = {code: index for index, code in enumerate(prepared.codes)}
    ranks = pd.Series([raw[code] for code in selected]).rank(
        method="average", pct=True
    ).to_numpy(dtype=float)
    strength = np.maximum(ranks, 1.0e-6)
    selected_weights = strength / float(strength.sum())
    weights = np.zeros(len(prepared.codes), dtype=float)
    for code, value in zip(selected, selected_weights):
        weights[position[code]] = float(value)
    active = weights - prepared.benchmark
    trade = weights - prepared.previous
    turnover = 0.5 * (
        float(np.abs(trade).sum())
        + prepared.outside_previous_weight
        + prepared.cash_previous_weight
    )
    alpha_utility = float(prepared.config.alpha_weight * (prepared.alpha @ weights))
    transaction_cost = float(prepared.config.transaction_cost_rate * turnover)
    # Baseline flags are serialized in the payload below.
    # It remains independent from the constrained optimizer result.
    # It is never used as a fallback.
    return {
        "name": "top_score_50_pure_long_only",
        "tradable": True,
        "comparison_only": True,
        "used_as_optimizer_fallback": False,
        "construction": "top_k_positive_rank_score_proportional_long_only",
        "same_alpha_transform_as_optimizer": True,
        "same_transaction_cost_rate_as_optimizer": True,
        "constraint_free_except_investable_black_white_lists": True,
        "holdings_count": int(np.sum(weights > 0.0)),
        "weights": {code: float(weights[index]) for index, code in enumerate(prepared.codes) if weights[index] > 0.0},
        "alpha_utility": alpha_utility,
        "one_way_turnover": turnover,
        "transaction_cost": transaction_cost,
        "alpha_after_transaction_cost": alpha_utility - transaction_cost,
        "estimated_tracking_error": float(np.linalg.norm(prepared.risk_root @ active)),
        "active_share": 0.5 * float(np.abs(active).sum()),
        "weight_hash": _sha256_parts(selected, weights),
    }


def optimize_stock_portfolio(
    cross_section: pd.DataFrame,
    *,
    style_exposures: pd.DataFrame | Mapping[str, Any] | Sequence[str] | None = None,
    annual_covariance: np.ndarray | pd.DataFrame | None = None,
    risk_root: np.ndarray | pd.DataFrame | None = None,
    risk_asset_codes: Sequence[str] | None = None,
    previous_weights: Mapping[str, float] | pd.Series | None = None,
    config: StockOptimizerConfig | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return certified weights or an explicit non-tradable blocked result."""

    raw_config = config
    try:
        config = _coerce_config(config)
        prepared = _prepare(
            cross_section,
            style_exposures=style_exposures,
            annual_covariance=annual_covariance,
            risk_root=risk_root,
            risk_asset_codes=risk_asset_codes,
            previous_weights=previous_weights,
            config=config,
        )
    except (ValueError, TypeError) as exc:
        return _blocked(
            "input_contract",
            str(exc),
            requested={"config": _config_payload(raw_config)},
            relaxation=[_relaxation_hint("input_contract", str(exc), None)],
        )

    if prepared.precheck["status"] != "passed":
        return _blocked(
            "feasibility_precheck",
            "deterministic_precheck_failed",
            requested=prepared.requested,
            precheck=prepared.precheck,
            relaxation=prepared.precheck["minimum_relaxation"],
        )
    if not _highs_milp_available():
        return _blocked(
            "solver_availability",
            "certified_support_solver_highs_milp_unavailable",
            requested=prepared.requested,
            precheck=prepared.precheck,
            solver={
                "required_phase_i": "SCIPY_HIGHS_MILP",
                "available": False,
                "heuristic_support_fallback_used": False,
            },
        )
    if cp is None or "CLARABEL" not in cp.installed_solvers():
        return _blocked(
            "solver_availability",
            "certified_solver_clarabel_unavailable",
            requested=prepared.requested,
            precheck=prepared.precheck,
            solver={
                "required_phase_i": "SCIPY_HIGHS_MILP",
                "required_phase_ii": "CLARABEL",
                "available": [] if cp is None else cp.installed_solvers(),
            },
        )

    prepared_before_search = prepared
    prepared, support_attempts = _search_feasible_support(prepared_before_search)
    if prepared is None:
        # No MILP or Clarabel trial vector is ever exposed as an order.
        original = _with_support_search_audit(
            prepared_before_search, support_attempts, None
        )
        last_attempt = support_attempts[-1] if support_attempts else {}
        linear_infeasible = (
            last_attempt.get("highs_status_code") == 2
            and last_attempt.get("clarabel_certification") == "not_run"
        )
        reason = (
            "highs_milp_proved_linear_mandate_infeasible"
            if linear_infeasible
            else "no_clarabel_certified_exact_support_within_highs_budget"
        )
        return _blocked(
            "support_search",
            reason,
            requested=original.requested,
            precheck=original.precheck,
            relaxation={
                "status": "not_emitted",
                "reason": "no_certified_support_for_support_specific_relaxation",
                "diagnostic_only": True,
                "weights_returned": False,
            },
            solver={
                "phase_i": "SCIPY_HIGHS_MILP",
                "phase_ii": "CLARABEL",
                "status": "support_search_exhausted",
                "heuristic_support_fallback_used": False,
            },
        )

    problem, variable, named_constraints, _ = _build_problem(prepared)
    started = time.perf_counter()
    try:
        problem.solve(
            solver="CLARABEL",
            verbose=False,
            max_iter=int(config.solver_max_iterations),
            tol_gap_abs=1.0e-9,
            tol_gap_rel=1.0e-9,
            tol_feas=1.0e-9,
        )
    except Exception as exc:  # pragma: no cover - solver-runtime specific.
        return _blocked(
            "solve",
            f"clarabel_exception:{type(exc).__name__}:{exc}",
            requested=prepared.requested,
            precheck=prepared.precheck,
            relaxation=_minimum_relaxation_socp(prepared),
            solver={"name": "CLARABEL", "status": "exception"},
        )
    solve_time_ms = (time.perf_counter() - started) * 1000.0
    solver_status = str(problem.status)
    solver_payload = {
        "name": "CLARABEL",
        "status": solver_status,
        "certified": False,
        "solve_time_ms": solve_time_ms,
        "iterations": int(getattr(problem.solver_stats, "num_iters", 0) or 0),
        "objective": None if problem.value is None else float(problem.value),
        "fallback_used": False,
    }
    if solver_status not in {"optimal", "optimal_inaccurate"} or variable.value is None:
        return _blocked(
            "solve",
            f"clarabel_nonoptimal:{solver_status}",
            requested=prepared.requested,
            precheck=prepared.precheck,
            relaxation=_minimum_relaxation_socp(prepared),
            solver=solver_payload,
        )

    raw_weights = np.asarray(variable.value, dtype=float).reshape(-1)
    if not np.isfinite(raw_weights).all():
        return _blocked(
            "post_solve_validation",
            "solver_returned_nonfinite_weights",
            requested=prepared.requested,
            precheck=prepared.precheck,
            relaxation=_minimum_relaxation_socp(prepared),
            solver=solver_payload,
        )
    # Preserve the certified candidate values; equality-constrained
    # non-candidates are serialized as exact zeros by contract.
    weights = raw_weights.copy()
    weights[prepared.noncandidate_index] = 0.0
    diagnostics = _solution_diagnostics(prepared, weights)
    tolerance = float(config.feasibility_tolerance)
    if diagnostics["max_constraint_violation"] > tolerance:
        solver_payload["max_constraint_violation"] = diagnostics["max_constraint_violation"]
        return _blocked(
            "post_solve_validation",
            "certified_solution_failed_independent_residual_check",
            requested=prepared.requested,
            precheck=prepared.precheck,
            relaxation=_minimum_relaxation_socp(prepared),
            solver=solver_payload,
        )
    solver_payload["certified"] = True
    solver_payload["certification_policy"] = (
        "optimal_exact" if solver_status == "optimal"
        else "optimal_inaccurate_accepted_only_after_independent_residual_check"
    )

    dual: dict[str, Any] = {}
    for name, constraint in named_constraints.items():
        labels: Sequence[str] | None = None
        if name in {"candidate_minimum", "candidate_maximum"}:
            labels = prepared.candidate_codes
        elif name in {"active_upper", "active_lower"}:
            labels = prepared.codes
        elif name == "noncandidate_zero":
            labels = [prepared.codes[index] for index in prepared.noncandidate_index]
        elif name in {"buy_limits", "sell_limits"}:
            limits = prepared.buy_limits if name == "buy_limits" else prepared.sell_limits
            labels = [prepared.codes[index] for index in np.flatnonzero(np.isfinite(limits))]
        dual[name] = _dual_payload(constraint, labels)

    active = diagnostics["active"]
    trade = diagnostics["trade"]
    transactions = [
        {
            "ts_code": code,
            "previous_weight": float(prepared.previous[index]),
            "target_weight": float(weights[index]),
            "trade_weight": float(trade[index]),
            "side": "buy" if trade[index] > tolerance else "sell" if trade[index] < -tolerance else "hold",
        }
        for index, code in enumerate(prepared.codes)
        if abs(float(trade[index])) > tolerance
    ]
    transactions.extend(
        {
            "ts_code": code,
            "asset_scope": "outside_current_universe",
            "previous_weight": float(value),
            "target_weight": 0.0,
            "trade_weight": -float(value),
            "side": "sell",
        }
        for code, value in sorted(prepared.outside_previous_positions.items())
        if float(value) > tolerance
    )
    if prepared.cash_previous_weight > tolerance:
        transactions.append({
            "ts_code": "CASH",
            "asset_scope": "cash_leg",
            "previous_weight": float(prepared.cash_previous_weight),
            "target_weight": 0.0,
            "trade_weight": -float(prepared.cash_previous_weight),
            "side": "deploy_cash",
        })
    output_weights = {code: float(weights[index]) for index, code in enumerate(prepared.codes)}
    output_active = {code: float(active[index]) for index, code in enumerate(prepared.codes)}
    solution_hash = _sha256_parts(prepared.input_hash, weights, active, trade)
    alpha_utility = float(config.alpha_weight * (prepared.alpha @ weights))
    score_target = diagnostics["score_target"]
    score_target_cost = float(
        config.score_target_penalty
        * np.square(weights - score_target).sum()
    )
    active_variance = float(np.square(prepared.risk_root @ active).sum())
    turnover_cost = float(config.transaction_cost_rate * diagnostics["turnover"])
    turnover_l1_cost = float(config.turnover_l1_penalty * diagnostics["turnover"])
    turnover_l2_cost = float(config.turnover_l2_penalty * np.square(trade).sum())
    active_risk_cost = float(config.active_risk_penalty * active_variance)
    objective_utility = float(
        alpha_utility - score_target_cost - active_risk_cost - turnover_cost
        - turnover_l1_cost - turnover_l2_cost
    )
    baseline = top_score_long_only_baseline(prepared)
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "status": "ready",
        "tradable": True,
        "requested": prepared.requested,
        "precheck": prepared.precheck,
        "solver": {
            **solver_payload,
            "max_constraint_violation": diagnostics["max_constraint_violation"],
            "cvxpy_version": getattr(cp, "__version__", None),
            "installed_solvers": cp.installed_solvers(),
        },
        "risk_diagnostics": prepared.risk_diagnostics,
        "weights": output_weights,
        "active_weights": output_active,
        "transactions": transactions,
        "realized": {
            "holdings_count": diagnostics["holdings_count"],
            "weight_sum": float(weights.sum()),
            "noncandidate_positive_count": int(np.sum(weights[prepared.noncandidate_index] != 0.0)),
            "tracking_error": diagnostics["tracking_error"],
            "active_share": 0.5 * float(np.abs(active).sum()),
            "one_way_turnover": diagnostics["turnover"],
            "security_buy_weight": float(diagnostics["buy"].sum()),
            "security_sell_weight": float(diagnostics["sell"].sum() + prepared.outside_previous_weight),
            "transaction_cost": turnover_cost,
            "turnover_l1_penalty_cost": turnover_l1_cost,
            "turnover_l2_penalty_cost": turnover_l2_cost,
            "active_risk_penalty_cost": active_risk_cost,
            "score_target_penalty_cost": score_target_cost,
            "score_target_l2_distance": diagnostics[
                "score_target_l2_distance"
            ],
            "score_target_definition": (
                "certified_support_score_percentile_rank_proportional"
            ),
            "objective_utility_after_all_penalties": objective_utility,
            "objective_cost_components_reconciled": True,
            "alpha_utility": alpha_utility,
            "active_variance": active_variance,
            "industry_active_exposure": diagnostics["industry_active"],
            "style_active_exposure": diagnostics["style_active"],
            "execution_exceptions": list(prepared.execution_exceptions),
            "execution_exception_count": len(
                prepared.execution_exceptions
            ),
            "candidate_count": len(prepared.candidate_codes),
            "full_active_vector_count": len(output_active),
        },
        "slack": diagnostics["slack"],
        "dual": dual,
        "baseline": baseline,
        "comparison": {
            "optimizer_alpha_after_cost": alpha_utility - turnover_cost,
            "baseline_alpha_after_cost": baseline["alpha_after_transaction_cost"],
            "optimizer_minus_baseline_alpha_after_cost": (
                alpha_utility - turnover_cost - baseline["alpha_after_transaction_cost"]
            ),
            "optimizer_tracking_error": diagnostics["tracking_error"],
            "baseline_tracking_error": baseline["estimated_tracking_error"],
            "interpretation": "same_signal_and_cost_units; baseline_is_unconstrained_and_not_a_tradable_recommendation",
        },
        "input_hash": prepared.input_hash,
        "constraint_hash": _sha256_parts(prepared.requested),
        "solution_hash": solution_hash,
        "fallback_used": False,
        "fallback_policy": "forbidden",
    }


def build_psd_factor_risk_root(
    matured_returns: pd.DataFrame,
    cross_section: pd.DataFrame,
    *,
    style_exposures: pd.DataFrame | Mapping[str, Any] | Sequence[str] | None = None,
    signal_date: str | None = None,
    annualization: float = 252.0,
    half_life: float = 63.0,
    diagonal_shrinkage: float = 0.30,
    ridge: float = 1.0e-5,
    eigenvalue_floor: float = 1.0e-8,
    minimum_row_coverage: float = 0.80,
    minimum_asset_coverage: float = 0.60,
    minimum_factor_observations: int = 20,
    minimum_asset_observations: int = 20,
    specific_variance_prior_observations: float = 60.0,
    allow_ipo_specific_risk_prior: bool = False,
    date_index_kind: str = "maturity_date",
) -> dict[str, Any]:
    """Build a missing-aware PSD industry/style factor risk root.

    Factor returns are estimated each day from that day's genuinely observed
    stock returns only.  Individual missing returns are never zero-filled,
    cross-sectionally filled, or backfilled.  Each stock's specific variance is
    estimated from its own observed residuals; a short-history estimate is only
    lifted toward a conservative industry/global prior, never reduced by that
    prior.  All dated observations must mature strictly before ``signal_date``.
    """
    if not isinstance(matured_returns, pd.DataFrame):
        raise ValueError("matured_returns_must_be_dataframe")
    if not isinstance(cross_section, pd.DataFrame):
        raise ValueError("cross_section_must_be_dataframe")
    annualization = _finite_float(annualization, "annualization")
    half_life = _finite_float(half_life, "half_life")
    diagonal_shrinkage = _finite_float(
        diagonal_shrinkage, "diagonal_shrinkage"
    )
    ridge = _finite_float(ridge, "ridge")
    eigenvalue_floor = _finite_float(eigenvalue_floor, "eigenvalue_floor")
    minimum_row_coverage = _finite_float(
        minimum_row_coverage, "minimum_row_coverage"
    )
    minimum_asset_coverage = _finite_float(
        minimum_asset_coverage, "minimum_asset_coverage"
    )
    specific_variance_prior_observations = _finite_float(
        specific_variance_prior_observations,
        "specific_variance_prior_observations",
    )
    for name, value in (
        ("minimum_factor_observations", minimum_factor_observations),
        ("minimum_asset_observations", minimum_asset_observations),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError(f"{name}_must_be_integer")
        if int(value) < 20:
            raise ValueError(f"{name}_must_be_at_least_20")
    minimum_factor_observations = int(minimum_factor_observations)
    minimum_asset_observations = int(minimum_asset_observations)
    if annualization <= 0.0:
        raise ValueError("annualization_must_be_positive")
    if half_life <= 0.0:
        raise ValueError("half_life_must_be_positive")
    if not 0.0 <= diagonal_shrinkage <= 1.0:
        raise ValueError("diagonal_shrinkage_must_be_between_zero_and_one")
    if ridge < 0.0:
        raise ValueError("ridge_must_be_nonnegative")
    if eigenvalue_floor <= 0.0:
        raise ValueError("eigenvalue_floor_must_be_positive")
    if not 0.0 < minimum_row_coverage <= 1.0:
        raise ValueError("minimum_row_coverage_must_be_in_zero_one")
    if not 0.0 < minimum_asset_coverage <= 1.0:
        raise ValueError("minimum_asset_coverage_must_be_in_zero_one")
    if specific_variance_prior_observations <= 0.0:
        raise ValueError("specific_variance_prior_observations_must_be_positive")
    date_index_kind = str(date_index_kind).strip().lower()
    if date_index_kind not in {"maturity_date", "return_end_date"}:
        raise ValueError("date_index_kind_must_be_maturity_or_return_end_date")

    required = {"ts_code", "industry"}
    if not required.issubset(cross_section.columns):
        raise ValueError("cross_section_requires_ts_code_and_industry")
    frame = cross_section.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame = frame.sort_values("ts_code", kind="mergesort").reset_index(drop=True)
    codes = frame["ts_code"].tolist()
    if frame["ts_code"].duplicated().any():
        raise ValueError("duplicate_ts_code")
    returns = matured_returns.copy()
    returns.columns = returns.columns.astype(str)
    if returns.columns.duplicated().any():
        raise ValueError("matured_returns_duplicate_asset_columns")
    missing = [code for code in codes if code not in returns.columns]
    if missing:
        raise ValueError("matured_returns_missing_assets:" + ",".join(missing[:12]))
    returns = returns.reindex(columns=codes).astype(float)
    returns = returns.replace([np.inf, -np.inf], np.nan)
    if len(returns) < minimum_factor_observations:
        raise ValueError(
            "risk_history_factor_observations_below_minimum:"
            f"{len(returns)}<{minimum_factor_observations}"
        )
    observed = pd.to_datetime(pd.Index(returns.index), errors="coerce")
    if not observed.notna().all():
        raise ValueError("risk_history_index_contains_unparseable_dates")
    if observed.duplicated().any():
        raise ValueError("risk_history_contains_duplicate_maturity_dates")
    order = np.argsort(observed.to_numpy())
    returns = returns.iloc[order].copy()
    observed = observed[order]
    if signal_date is not None:
        signal = pd.to_datetime(signal_date, errors="raise")
        if observed.max() >= signal:
            raise ValueError("risk_history_contains_unmatured_or_signal_date_return")
    row_coverage = returns.notna().mean(axis=1)
    asset_coverage = returns.notna().mean(axis=0)
    if float(row_coverage.min()) < minimum_row_coverage:
        raise ValueError("risk_history_row_coverage_below_minimum")
    total_coverage = float(returns.notna().to_numpy().mean())

    styles = _strict_style_frame(frame, style_exposures)
    industry = pd.get_dummies(
        frame["industry"].astype(str), prefix="industry", dtype=float
    )
    raw_blocks = [industry.to_numpy(dtype=float)]
    raw_factor_names = list(industry.columns)
    if len(styles.columns):
        style_values = styles.to_numpy(dtype=float)
        median = np.median(style_values, axis=0)
        mad = np.median(np.abs(style_values - median), axis=0)
        scale = np.where(1.4826 * mad > 1.0e-8, 1.4826 * mad, 1.0)
        raw_blocks.append(np.clip((style_values - median) / scale, -5.0, 5.0))
        raw_factor_names.extend(styles.columns.tolist())
    raw_exposure = np.column_stack(raw_blocks)

    # Deterministically remove exactly collinear style columns.  Industry
    # dummies enter first, so an identified industry basis is never displaced
    # by a redundant style transform.
    selected_columns: list[int] = []
    selected_factor_names: list[str] = []
    dropped_collinear_factors: list[str] = []
    current_rank = 0
    for column, factor_name in enumerate(raw_factor_names):
        candidate_columns = [*selected_columns, column]
        candidate_rank = int(np.linalg.matrix_rank(raw_exposure[:, candidate_columns]))
        if candidate_rank > current_rank:
            selected_columns.append(column)
            selected_factor_names.append(str(factor_name))
            current_rank = candidate_rank
        else:
            dropped_collinear_factors.append(str(factor_name))
    exposure = raw_exposure[:, selected_columns]
    factor_names = selected_factor_names
    required_factor_rank = int(exposure.shape[1])
    minimum_daily_cross_section = min(
        len(codes),
        max(
            required_factor_rank,
            int(math.ceil(minimum_row_coverage * len(codes))),
        ),
    )

    values = returns.to_numpy(dtype=float)
    factor_return_rows: list[np.ndarray] = []
    factor_row_positions: list[int] = []
    daily_sample_counts: list[int] = []
    daily_factor_ranks: list[int] = []
    skipped_rank_dates: list[str] = []
    residual = np.full(values.shape, np.nan, dtype=float)
    ridge_matrix = np.eye(required_factor_rank, dtype=float) * float(ridge)
    for row_index, row in enumerate(values):
        available = np.isfinite(row)
        sample_count = int(available.sum())
        daily_sample_counts.append(sample_count)
        if sample_count < minimum_daily_cross_section:
            raise ValueError(
                "risk_history_daily_cross_section_below_minimum:"
                f"{observed[row_index].strftime('%Y-%m-%d')}:"
                f"{sample_count}<{minimum_daily_cross_section}"
            )
        day_exposure = exposure[available]
        day_rank = int(np.linalg.matrix_rank(day_exposure))
        daily_factor_ranks.append(day_rank)
        if day_rank < required_factor_rank:
            skipped_rank_dates.append(observed[row_index].strftime("%Y-%m-%d"))
            continue
        normal = day_exposure.T @ day_exposure + ridge_matrix
        factor_return = np.linalg.solve(normal, day_exposure.T @ row[available])
        fitted = day_exposure @ factor_return
        factor_return_rows.append(factor_return)
        factor_row_positions.append(row_index)
        residual[row_index, available] = row[available] - fitted
    if len(factor_return_rows) < minimum_factor_observations:
        raise ValueError(
            "risk_history_identified_factor_observations_below_minimum:"
            f"{len(factor_return_rows)}<{minimum_factor_observations}"
        )
    factor_returns = np.vstack(factor_return_rows)

    full_age = np.arange(len(returns) - 1, -1, -1, dtype=float)
    full_weights = np.power(0.5, full_age / half_life)
    factor_weights = full_weights[np.asarray(factor_row_positions, dtype=int)]
    factor_weights /= float(factor_weights.sum())
    factor_mean = factor_weights @ factor_returns
    centered_factor = factor_returns - factor_mean
    factor_covariance = (
        centered_factor.T @ (centered_factor * factor_weights[:, None])
    ) * float(annualization)

    residual_counts = np.isfinite(residual).sum(axis=0).astype(int)
    below_specific_floor = [
        f"{code}:{int(residual_counts[index])}"
        for index, code in enumerate(codes)
        if int(residual_counts[index]) < minimum_asset_observations
    ]
    if below_specific_floor and not allow_ipo_specific_risk_prior:
        raise ValueError(
            "risk_history_asset_observations_below_minimum:"
            + ",".join(below_specific_floor[:20])
        )
    raw_residual_variance = np.full(len(codes), np.nan, dtype=float)
    for column in range(len(codes)):
        available = np.isfinite(residual[:, column])
        if int(available.sum()) < 2:
            continue
        asset_weights = full_weights[available]
        asset_weights /= float(asset_weights.sum())
        asset_residual = residual[available, column]
        asset_mean = float(asset_weights @ asset_residual)
        raw_residual_variance[column] = float(
            asset_weights @ np.square(asset_residual - asset_mean)
        ) * float(annualization)

    identified_observations = len(factor_return_rows)
    seasoned_floor = max(
        minimum_asset_observations,
        int(math.ceil(minimum_asset_coverage * identified_observations)),
    )
    seasoned = (
        (residual_counts >= seasoned_floor)
        & np.isfinite(raw_residual_variance)
    )
    prior_pool = raw_residual_variance[seasoned]
    if not len(prior_pool):
        prior_pool = raw_residual_variance[
            np.isfinite(raw_residual_variance)
        ]
    if not len(prior_pool):
        raise ValueError(
            "risk_history_no_seasoned_specific_variance_prior_pool"
        )
    global_conservative_prior = float(np.quantile(prior_pool, 0.75))
    industry_values = frame["industry"].astype(str).to_numpy()
    specific_variance = raw_residual_variance.copy()
    short_history_audit: list[dict[str, Any]] = []
    prior_source_counts = {"industry_q75": 0, "global_q75": 0}
    prior_strengths = np.zeros(len(codes), dtype=float)
    for column, code in enumerate(codes):
        same_industry = (industry_values == industry_values[column]) & seasoned
        if int(same_industry.sum()) >= 3:
            group_prior = float(np.quantile(
                raw_residual_variance[same_industry], 0.75
            ))
            prior_source = "industry_q75"
        else:
            group_prior = global_conservative_prior
            prior_source = "global_q75"
        prior_source_counts[prior_source] += 1
        conservative_prior = max(group_prior, global_conservative_prior)
        observed_specific = (
            float(raw_residual_variance[column])
            if math.isfinite(float(raw_residual_variance[column]))
            else conservative_prior
        )
        missing_fraction = max(
            0.0, 1.0 - residual_counts[column] / float(identified_observations)
        )
        prior_strength = (
            specific_variance_prior_observations
            / (residual_counts[column] + specific_variance_prior_observations)
        ) * missing_fraction
        prior_strengths[column] = prior_strength
        blended = (
            (1.0 - prior_strength) * observed_specific
            + prior_strength * conservative_prior
        )
        # A conservative short-history prior may raise, but never suppress, an
        # unusually high stock-specific variance estimate.
        specific_variance[column] = max(
            observed_specific, blended
        )
        if residual_counts[column] < identified_observations:
            short_history_audit.append({
                "ts_code": code,
                "observations": int(residual_counts[column]),
                "seasoned_observation_floor": seasoned_floor,
                "raw_specific_variance": (
                    float(raw_residual_variance[column])
                    if math.isfinite(float(raw_residual_variance[column]))
                    else None
                ),
                "raw_specific_variance_available": bool(
                    math.isfinite(float(raw_residual_variance[column]))
                ),
                "conservative_prior": conservative_prior,
                "prior_source": prior_source,
                "prior_strength": float(prior_strength),
                "final_specific_variance": float(specific_variance[column]),
            })

    covariance = exposure @ factor_covariance @ exposure.T + np.diag(
        np.maximum(specific_variance, 0.0)
    )
    shrinkage = diagonal_shrinkage
    covariance = (
        (1.0 - shrinkage) * covariance
        + shrinkage * np.diag(np.diag(covariance))
    )
    covariance = (covariance + covariance.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    floor = max(float(eigenvalue_floor), float(np.max(eigenvalues)) * 1.0e-8)
    repaired = np.maximum(eigenvalues, floor)
    covariance = (eigenvectors * repaired) @ eigenvectors.T
    covariance = (covariance + covariance.T) / 2.0
    risk_root = np.sqrt(repaired)[:, None] * eigenvectors.T
    return {
        "risk_root": risk_root,
        "annual_covariance": covariance,
        "codes": codes,
        "risk_asset_codes": codes,
        "factor_names": factor_names,
        "diagnostics": {
            "method": "observed_only_daily_industry_style_factor_covariance",
            "observations": int(identified_observations),
            "input_observations": int(len(returns)),
            "date_index_kind": date_index_kind,
            "earliest_maturity_date": observed.min().strftime("%Y-%m-%d"),
            "latest_maturity_date": observed.max().strftime("%Y-%m-%d"),
            "minimum_row_coverage": float(row_coverage.min()),
            "minimum_asset_coverage": float(asset_coverage.min()),
            "total_coverage_before_regression": total_coverage,
            "missing_value_policy": (
                "observed_returns_only_daily_cross_sectional_regression;"
                "no_individual_return_imputation"
            ),
            "individual_return_imputation_used": False,
            "zero_fill_used": False,
            "backfill_used": False,
            "daily_factor_regression": "ridge_cross_sectional_observed_assets_only",
            "minimum_daily_cross_section_observations": minimum_daily_cross_section,
            "daily_cross_section_observations_min": int(min(daily_sample_counts)),
            "daily_cross_section_observations_median": float(np.median(daily_sample_counts)),
            "daily_cross_section_observations_max": int(max(daily_sample_counts)),
            "required_daily_factor_rank": required_factor_rank,
            "daily_factor_rank_min": int(min(daily_factor_ranks)),
            "daily_factor_rank_max": int(max(daily_factor_ranks)),
            "rank_deficient_dates_skipped": skipped_rank_dates,
            "rank_deficient_date_count": len(skipped_rank_dates),
            "raw_factor_count": len(raw_factor_names),
            "identified_factor_count": required_factor_rank,
            "dropped_exactly_collinear_factors": dropped_collinear_factors,
            "minimum_factor_observations": minimum_factor_observations,
            "minimum_asset_observations": minimum_asset_observations,
            "minimum_specific_residual_observations": int(residual_counts.min()),
            "maximum_specific_residual_observations": int(residual_counts.max()),
            "seasoned_asset_observation_floor": seasoned_floor,
            "seasoned_asset_count": int(seasoned.sum()),
            "short_history_asset_count": len(short_history_audit),
            "short_history_assets": short_history_audit,
            "specific_variance_prior": (
                "max(industry_seasoned_q75,global_seasoned_q75);"
                "missing_history_scaled_empirical_bayes;never_reduce_raw_variance"
            ),
            "ipo_specific_risk_prior_enabled": bool(
                allow_ipo_specific_risk_prior
            ),
            "ipo_prior_asset_count": len(below_specific_floor),
            "specific_variance_prior_observations": (
                specific_variance_prior_observations
            ),
            "global_conservative_specific_variance_prior": (
                global_conservative_prior
            ),
            "specific_variance_prior_source_counts": prior_source_counts,
            "maximum_specific_variance_prior_strength": float(
                prior_strengths.max()
            ),
            "specific_variance_hash": _sha256_parts(
                codes, residual_counts, raw_residual_variance, specific_variance
            ),
            "assets": int(len(codes)),
            "factors": int(len(factor_names)),
            "annualization": float(annualization),
            "half_life": float(half_life),
            "diagonal_shrinkage": shrinkage,
            "minimum_eigenvalue_before_repair": float(eigenvalues.min()),
            "minimum_eigenvalue_after_repair": float(repaired.min()),
            "point_in_time_policy": (
                "caller_supplies_only_matured_returns;"
                "signal_date_guard_enforced_when_dated"
            ),
            "signal_date": signal_date,
            "hash": _sha256_parts(codes, factor_names, risk_root),
        },
    }


__all__ = [
    "ENGINE_VERSION",
    "SCHEMA_VERSION",
    "StockOptimizerConfig",
    "build_psd_factor_risk_root",
    "optimize_stock_portfolio",
    "precheck_stock_problem",
    "top_score_long_only_baseline",
]
