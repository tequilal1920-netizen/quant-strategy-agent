"""Shared research-grade performance statistics.

Compounded annual return describes terminal wealth growth. Sharpe and the
information ratio use arithmetic period returns. Overlapping observations use
a HAC long-run variance estimate. Keeping these definitions here prevents the
standalone model workers from silently drifting onto incompatible metrics.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np


EPSILON = 1e-12


def finite_array(values: Iterable[float]) -> np.ndarray:
    array = np.asarray(list(values), dtype=float).reshape(-1)
    return array[np.isfinite(array)]


def compounded_annual_return(
    returns: Iterable[float],
    periods_per_year: float,
) -> float:
    values = finite_array(returns)
    if values.size == 0 or periods_per_year <= 0:
        return 0.0
    gross = float(np.prod(1.0 + values))
    if gross <= 0:
        return -1.0
    return float(gross ** (float(periods_per_year) / values.size) - 1.0)


def annualized_volatility(
    returns: Iterable[float],
    periods_per_year: float,
) -> float:
    values = finite_array(returns)
    if values.size < 2 or periods_per_year <= 0:
        return 0.0
    return float(np.std(values, ddof=1) * math.sqrt(float(periods_per_year)))


def annualized_sharpe(
    returns: Iterable[float],
    periods_per_year: float,
    annual_risk_free_rate: float = 0.0,
) -> float:
    """Arithmetic excess-return Sharpe at the requested sampling frequency."""

    values = finite_array(returns)
    if values.size < 2 or periods_per_year <= 0:
        return 0.0
    if annual_risk_free_rate <= -1.0:
        raise ValueError("annual_risk_free_rate must be greater than -1")
    period_risk_free = (1.0 + float(annual_risk_free_rate)) ** (
        1.0 / float(periods_per_year)
    ) - 1.0
    excess = values - period_risk_free
    denominator = float(np.std(excess, ddof=1))
    if denominator <= EPSILON:
        return 0.0
    return float(np.mean(excess) / denominator * math.sqrt(float(periods_per_year)))


def annualized_information_ratio(
    active_returns: Iterable[float],
    periods_per_year: float,
) -> float:
    values = finite_array(active_returns)
    if values.size < 2 or periods_per_year <= 0:
        return 0.0
    tracking_error = float(np.std(values, ddof=1))
    if tracking_error <= EPSILON:
        return 0.0
    return float(
        np.mean(values) / tracking_error * math.sqrt(float(periods_per_year))
    )


def automatic_hac_lag(observations: int, minimum_lag: int = 0) -> int:
    """Newey-West style bandwidth with an overlap-aware lower bound."""

    if observations < 2:
        return 0
    data_driven = int(math.floor(4.0 * (observations / 100.0) ** (2.0 / 9.0)))
    return min(observations - 1, max(0, int(minimum_lag), data_driven))


def newey_west_long_run_variance(
    values: Iterable[float],
    max_lag: int | None = None,
    minimum_lag: int = 0,
) -> tuple[float, int]:
    observations = finite_array(values)
    n = observations.size
    if n < 2:
        return 0.0, 0
    lag = (
        automatic_hac_lag(n, minimum_lag)
        if max_lag is None
        else min(n - 1, max(0, int(max_lag), int(minimum_lag)))
    )
    demeaned = observations - float(np.mean(observations))
    variance = float(np.dot(demeaned, demeaned) / n)
    for offset in range(1, lag + 1):
        covariance = float(np.dot(demeaned[offset:], demeaned[:-offset]) / n)
        bartlett_weight = 1.0 - offset / (lag + 1.0)
        variance += 2.0 * bartlett_weight * covariance
    return max(0.0, variance), lag


def hac_information_ratio(
    values: Iterable[float],
    periods_per_year: float,
    max_lag: int | None = None,
    minimum_lag: int = 0,
) -> float:
    observations = finite_array(values)
    if observations.size < 2 or periods_per_year <= 0:
        return 0.0
    variance, _ = newey_west_long_run_variance(
        observations,
        max_lag=max_lag,
        minimum_lag=minimum_lag,
    )
    if variance <= EPSILON:
        return 0.0
    return float(
        np.mean(observations)
        / math.sqrt(variance)
        * math.sqrt(float(periods_per_year))
    )


def hac_t_statistic(
    values: Iterable[float],
    max_lag: int | None = None,
    minimum_lag: int = 0,
) -> float:
    observations = finite_array(values)
    if observations.size < 2:
        return 0.0
    variance, _ = newey_west_long_run_variance(
        observations,
        max_lag=max_lag,
        minimum_lag=minimum_lag,
    )
    if variance <= EPSILON:
        return 0.0
    standard_error = math.sqrt(variance / observations.size)
    return float(np.mean(observations) / standard_error)


def effective_observations(
    values: Iterable[float],
    max_lag: int | None = None,
    minimum_lag: int = 0,
) -> float:
    observations = finite_array(values)
    n = observations.size
    if n < 2:
        return float(n)
    centered = observations - float(np.mean(observations))
    marginal_variance = float(np.dot(centered, centered) / n)
    long_run_variance, _ = newey_west_long_run_variance(
        observations,
        max_lag=max_lag,
        minimum_lag=minimum_lag,
    )
    if marginal_variance <= EPSILON or long_run_variance <= EPSILON:
        return float(n)
    estimate = n * marginal_variance / long_run_variance
    return float(min(n, max(1.0, estimate)))
