"""Leakage-safe cycle-view fitting utilities for v5.3 research.

``fit_cycle_view_model_v5`` maps the feature row at index ``t`` to the asset
return at ``t+1``.  A training mask must therefore be defined on target-return
months, not feature months.  This wrapper makes that alignment explicit and
refuses ambiguous lengths.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from cycle_views_v5 import fit_cycle_view_model_v5


def target_month_train_mask_v53(
    return_months: Sequence[str], train_end: str
) -> tuple[bool, ...]:
    """Return a feature-length mask whose item ``t`` gates target month ``t+1``."""

    months = tuple(str(month) for month in return_months)
    if len(months) < 2:
        raise ValueError("v53_cycle_view_months_too_short")
    if any(len(month) != 6 or not month.isdigit() for month in months):
        raise ValueError("v53_cycle_view_month_format_invalid")
    if tuple(sorted(months)) != months or len(set(months)) != len(months):
        raise ValueError("v53_cycle_view_months_must_be_unique_sorted")
    # fit_cycle_view_model_v5 drops the last mask item and uses mask[t] for the
    # pair x[t] -> returns[t+1].  Put the target-month decision in mask[t].
    decisions = [months[index + 1] <= train_end for index in range(len(months) - 1)]
    decisions.append(False)
    return tuple(decisions)


def fit_frozen_cycle_view_model_v53(
    asset_returns: np.ndarray,
    cycle_history: Sequence[Mapping[str, Any]],
    return_months: Sequence[str],
    *,
    train_end: str,
    minimum_train: int,
) -> dict[str, Any]:
    returns = np.asarray(asset_returns, dtype=float)
    if len(returns) != len(return_months) or len(returns) != len(cycle_history):
        raise ValueError("v53_cycle_view_inputs_misaligned")
    mask = target_month_train_mask_v53(return_months, train_end)
    fitted = fit_cycle_view_model_v5(
        returns,
        cycle_history,
        train_mask=mask,
        minimum_train=minimum_train,
    )
    output = dict(fitted)
    output["label_alignment"] = "cycle_features_at_t_predict_return_at_t_plus_1"
    output["last_admitted_target_month"] = max(
        (return_months[index + 1] for index in range(len(return_months) - 1) if mask[index]),
        default=None,
    )
    output["first_rejected_target_month"] = next(
        (return_months[index + 1] for index in range(len(return_months) - 1) if not mask[index]),
        None,
    )
    output["selection_uses_test"] = False
    return output


__all__ = ["fit_frozen_cycle_view_model_v53", "target_month_train_mask_v53"]
