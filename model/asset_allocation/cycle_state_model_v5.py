"""Causal explicit-duration state filtering for the v5 cycle models.

The implementation is deliberately small and auditable.  It expands every
economic state into ``(state, elapsed_month)`` cells, so persistence is driven
by an explicit duration distribution rather than an arbitrary HMM self-loop.
Transition learning, when requested, is stopped at ``train_end``; later rows
are filtered with frozen parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True)
class DurationPriorV5:
    """Truncated maximum-entropy duration prior, measured in months."""

    minimum_months: int
    expected_months: float
    maximum_months: int

    def validate(self) -> None:
        if self.minimum_months < 1:
            raise ValueError("duration_minimum_must_be_positive")
        if self.maximum_months < self.minimum_months:
            raise ValueError("duration_maximum_before_minimum")
        if not self.minimum_months <= self.expected_months <= self.maximum_months:
            raise ValueError("duration_expectation_outside_support")


@dataclass
class DurationFilterResultV5:
    state_probabilities: np.ndarray
    expected_elapsed_months: np.ndarray
    final_exit_transition: np.ndarray
    exit_transition_history: list[np.ndarray]
    learned_through: str | None
    diagnostics: dict[str, Any]


def duration_pmf_v5(prior: DurationPriorV5) -> np.ndarray:
    """Return the maximum-entropy PMF on the stated finite support.

    On a bounded integer support, the maximum-entropy distribution with a
    specified mean is an exponential tilt.  A deterministic bisection solves
    the tilt and therefore avoids hidden numerical fitting choices.
    """

    prior.validate()
    support = np.arange(prior.minimum_months, prior.maximum_months + 1, dtype=float)
    target = float(prior.expected_months)
    if len(support) == 1:
        return np.ones(1, dtype=float)

    def probabilities(tilt: float) -> np.ndarray:
        logits = tilt * (support - float(np.mean(support)))
        logits -= float(np.max(logits))
        values = np.exp(logits)
        return values / float(np.sum(values))

    lower, upper = -20.0, 20.0
    for _ in range(100):
        middle = (lower + upper) / 2.0
        mean = float(np.dot(probabilities(middle), support))
        if mean < target:
            lower = middle
        else:
            upper = middle
    return probabilities((lower + upper) / 2.0)


def duration_hazard_v5(prior: DurationPriorV5) -> np.ndarray:
    """Return discrete exit hazards for elapsed months 1..maximum_months."""

    pmf = duration_pmf_v5(prior)
    hazard = np.zeros(prior.maximum_months, dtype=float)
    survival = 1.0
    for duration in range(prior.minimum_months, prior.maximum_months + 1):
        probability = float(pmf[duration - prior.minimum_months])
        hazard[duration - 1] = min(max(probability / max(survival, 1.0e-15), 0.0), 1.0)
        survival -= probability
    hazard[-1] = 1.0
    return hazard


def _normalise_exit_transition(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=float).copy()
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("exit_transition_must_be_square")
    if np.any(~np.isfinite(matrix)) or np.any(matrix < 0.0):
        raise ValueError("exit_transition_must_be_nonnegative_finite")
    np.fill_diagonal(matrix, 0.0)
    row_sum = matrix.sum(axis=1)
    if np.any(row_sum <= 0.0):
        raise ValueError("exit_transition_row_without_destination")
    return matrix / row_sum[:, None]


def explicit_duration_filter_v5(
    log_likelihood: np.ndarray,
    exit_transition: np.ndarray,
    duration_priors: Sequence[DurationPriorV5],
    *,
    months: Sequence[str] | None = None,
    train_end: str | None = None,
    initial_probability: Sequence[float] | None = None,
    transition_prior_strength: float = 24.0,
) -> DurationFilterResultV5:
    """Filter state probabilities without using future observations.

    ``log_likelihood[t]`` may depend only on information available by month
    ``t``.  Exit-transition counts are updated from posterior exit flows only
    while ``month <= train_end``.  If ``train_end`` is omitted, parameters are
    kept at their declared priors; this is the safest default for callers that
    have not supplied an explicit training split.
    """

    likelihood = np.asarray(log_likelihood, dtype=float)
    if likelihood.ndim != 2 or likelihood.shape[0] < 1:
        raise ValueError("duration_filter_requires_time_by_state_likelihood")
    if np.any(~np.isfinite(likelihood)):
        raise ValueError("duration_filter_likelihood_not_finite")
    observations, states = likelihood.shape
    if len(duration_priors) != states:
        raise ValueError("duration_prior_state_count_mismatch")
    for prior in duration_priors:
        prior.validate()
    if months is None:
        month_labels = [f"{index:06d}" for index in range(observations)]
    else:
        month_labels = [str(month) for month in months]
        if len(month_labels) != observations:
            raise ValueError("duration_filter_month_count_mismatch")
    if transition_prior_strength <= 0.0:
        raise ValueError("transition_prior_strength_must_be_positive")

    transition = _normalise_exit_transition(exit_transition)
    maximum_duration = max(prior.maximum_months for prior in duration_priors)
    hazards = np.zeros((states, maximum_duration), dtype=float)
    for state, prior in enumerate(duration_priors):
        values = duration_hazard_v5(prior)
        hazards[state, : len(values)] = values
        hazards[state, len(values):] = 1.0

    if initial_probability is None:
        initial = np.full(states, 1.0 / states, dtype=float)
    else:
        initial = np.asarray(initial_probability, dtype=float)
        if initial.shape != (states,) or np.any(initial < 0.0) or float(initial.sum()) <= 0.0:
            raise ValueError("invalid_duration_initial_probability")
        initial = initial / float(initial.sum())

    expanded = np.zeros((states, maximum_duration), dtype=float)
    first_emission = np.exp(likelihood[0] - float(np.max(likelihood[0])))
    expanded[:, 0] = initial * first_emission
    expanded /= float(expanded.sum())

    state_probability = np.zeros((observations, states), dtype=float)
    expected_elapsed = np.zeros((observations, states), dtype=float)
    transition_history: list[np.ndarray] = []
    counts = transition_prior_strength * transition
    learned_through: str | None = None

    def record(position: int) -> None:
        probability = expanded.sum(axis=1)
        state_probability[position] = probability
        elapsed = np.arange(1, maximum_duration + 1, dtype=float)
        numerator = expanded @ elapsed
        expected_elapsed[position] = np.divide(
            numerator,
            probability,
            out=np.zeros_like(numerator),
            where=probability > 1.0e-15,
        )
        transition_history.append(transition.copy())

    record(0)
    learned_exit_mass = 0.0
    for position in range(1, observations):
        emission = np.exp(likelihood[position] - float(np.max(likelihood[position])))
        predicted = np.zeros_like(expanded)
        raw_exit_flow = np.zeros((states, states), dtype=float)

        for source in range(states):
            for elapsed_index in range(maximum_duration):
                mass = float(expanded[source, elapsed_index])
                if mass <= 0.0:
                    continue
                hazard = float(hazards[source, elapsed_index])
                if elapsed_index + 1 < maximum_duration:
                    predicted[source, elapsed_index + 1] += mass * (1.0 - hazard)
                exit_mass = mass * hazard
                if exit_mass > 0.0:
                    raw_exit_flow[source] += exit_mass * transition[source]
        predicted[:, 0] += raw_exit_flow.sum(axis=0)
        predicted *= emission[:, None]
        normaliser = float(predicted.sum())
        if normaliser <= 1.0e-300:
            raise ValueError("duration_filter_zero_posterior_mass")
        expanded = predicted / normaliser

        # Approximate posterior exit flows use the same destination emission
        # and global normaliser as the forward recursion.  Counts are a soft,
        # auditable online update and never use a later observation.
        posterior_exit_flow = raw_exit_flow * emission[None, :] / normaliser
        may_learn = train_end is not None and month_labels[position] <= str(train_end)
        if may_learn:
            counts += posterior_exit_flow
            np.fill_diagonal(counts, 0.0)
            transition = _normalise_exit_transition(counts)
            learned_through = month_labels[position]
            learned_exit_mass += float(posterior_exit_flow.sum())
        record(position)

    return DurationFilterResultV5(
        state_probabilities=state_probability,
        expected_elapsed_months=expected_elapsed,
        final_exit_transition=transition,
        exit_transition_history=transition_history,
        learned_through=learned_through,
        diagnostics={
            "method": "explicit_duration_hidden_semi_markov_forward_filter",
            "causal": True,
            "parameter_learning_policy": "soft exit-transition counts update only through train_end; fixed prior when train_end is absent",
            "train_end": train_end,
            "learned_through": learned_through,
            "learned_exit_mass": learned_exit_mass,
            "transition_prior_strength": float(transition_prior_strength),
            "duration_priors": [
                {
                    "minimum_months": prior.minimum_months,
                    "expected_months": prior.expected_months,
                    "maximum_months": prior.maximum_months,
                }
                for prior in duration_priors
            ],
        },
    )


__all__ = [
    "DurationFilterResultV5",
    "DurationPriorV5",
    "duration_hazard_v5",
    "duration_pmf_v5",
    "explicit_duration_filter_v5",
]
