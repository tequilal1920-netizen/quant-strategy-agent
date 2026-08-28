"""Auditable Euler decomposition for the v5.1 macro-factor risk model.

The covariance used by the allocation solver is

    Sigma = rho * (B F B' + D) + (1-rho) * Sigma_stat.

For portfolio weights ``w`` and factor exposure ``x = B' w``, factor ``j``
contributes ``rho * x_j * (F x)_j`` to portfolio variance.  The specific and
statistical covariance components are reported separately, together with a
small projection/reconciliation residual.  No contribution is invented when
PIT macro factors are not admitted: ``rho`` is then zero and the audit says so.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence

import numpy as np


def _matrix(payload: Mapping[str, Any], key: str) -> np.ndarray:
    value = np.asarray(payload.get(key), dtype=float)
    if value.ndim != 2 or value.shape[0] != value.shape[1]:
        raise ValueError(f"invalid_square_matrix:{key}")
    if not np.all(np.isfinite(value)):
        raise ValueError(f"non_finite_matrix:{key}")
    return value


def _weight_vector(
    weights: Mapping[str, Any], asset_order: Sequence[str]
) -> np.ndarray:
    vector = np.asarray([float(weights[name]) for name in asset_order], dtype=float)
    if vector.ndim != 1 or not np.all(np.isfinite(vector)):
        raise ValueError("invalid_weight_vector")
    if abs(float(vector.sum()) - 1.0) > 1.0e-6:
        raise ValueError("weight_sum_not_one")
    return vector


def macro_factor_risk_decomposition_v51(
    weights: Mapping[str, Any],
    asset_order: Sequence[str],
    covariance_payload: Mapping[str, Any],
) -> dict[str, Any]:
    covariance = _matrix(covariance_payload, "covariance")
    factor_covariance = _matrix(covariance_payload, "factor_covariance")
    specific = _matrix(covariance_payload, "specific_covariance")
    statistical = _matrix(covariance_payload, "statistical_covariance")
    loadings = np.asarray(covariance_payload.get("factor_loadings"), dtype=float)
    factor_names = [str(name) for name in covariance_payload.get("factor_names") or []]
    weight = _weight_vector(weights, asset_order)
    if covariance.shape != (len(weight), len(weight)):
        raise ValueError("covariance_and_weights_do_not_align")
    if loadings.shape != (len(weight), factor_covariance.shape[0]):
        raise ValueError("factor_loadings_do_not_align")
    if len(factor_names) != factor_covariance.shape[0]:
        raise ValueError("factor_names_do_not_align")
    if specific.shape != covariance.shape or statistical.shape != covariance.shape:
        raise ValueError("covariance_components_do_not_align")

    rho = float(covariance_payload.get("macro_blend_weight") or 0.0)
    if not 0.0 <= rho <= 1.0:
        raise ValueError("macro_blend_weight_out_of_range")
    exposure = loadings.T @ weight
    marginal_factor = factor_covariance @ exposure
    factor_variance = rho * exposure * marginal_factor
    specific_variance = rho * float(weight @ specific @ weight)
    statistical_variance = (1.0 - rho) * float(weight @ statistical @ weight)
    total_variance = float(weight @ covariance @ weight)
    component_sum = float(factor_variance.sum()) + specific_variance + statistical_variance
    reconciliation = total_variance - component_sum
    denominator = total_variance if abs(total_variance) > 1.0e-18 else 1.0
    active = bool(rho > 0.0 and np.any(np.abs(loadings) > 1.0e-14))

    rows: list[dict[str, Any]] = []
    for index, name in enumerate(factor_names):
        contribution = float(factor_variance[index])
        rows.append(
            {
                "factor": name,
                "exposure": float(exposure[index]),
                "marginal_factor_variance": float(marginal_factor[index]),
                "variance_contribution": contribution,
                "risk_contribution": contribution / denominator,
                "detail": (
                    "Euler: rho*x_j*(F*x)_j"
                    if active
                    else "PIT macro gate blocked; rho=0, contribution is exactly zero"
                ),
            }
        )
    for name, contribution, detail in (
        (
            "macro_specific_risk",
            specific_variance,
            "rho*w'Dw; asset-specific variance inside the macro factor model",
        ),
        (
            "statistical_covariance_risk",
            statistical_variance,
            "(1-rho)*w'Sigma_stat*w; causal EWMA/shrinkage component",
        ),
        (
            "psd_projection_reconciliation",
            reconciliation,
            "total variance less serialized component sum; should be numerical only",
        ),
    ):
        rows.append(
            {
                "factor": name,
                "variance_contribution": float(contribution),
                "risk_contribution": float(contribution) / denominator,
                "detail": detail,
            }
        )

    return {
        "status": "active" if active else "inactive_by_pit_gate",
        "method": "Euler_decomposition_of_rho_BFBt_plus_D_and_statistical_covariance",
        "formula": "Sigma=rho*(BFB'+D)+(1-rho)*Sigma_stat; c_j=rho*x_j*(F*x)_j",
        "asset_order": list(asset_order),
        "factor_names": factor_names,
        "macro_blend_weight": rho,
        "portfolio_variance": total_variance,
        "component_variance_sum": component_sum + reconciliation,
        "relative_contribution_sum": float(sum(row["risk_contribution"] for row in rows)),
        "factor_exposure": {
            name: float(exposure[index]) for index, name in enumerate(factor_names)
        },
        "rows": rows,
        "production_interpretation": (
            "active macro factor risk model"
            if active
            else "macro factors are structurally implemented but contribute zero until PIT admission"
        ),
    }


def attach_factor_risk_audit_v51(source: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(source))
    asset_order = list(payload.get("asset_order") or [])
    allocations = payload.get("allocations") or {}
    recommended = allocations.get("recommended") or {}
    metadata = recommended.get("metadata") or {}
    covariance = metadata.get("covariance") or {}
    audit = macro_factor_risk_decomposition_v51(
        recommended.get("weights") or {}, asset_order, covariance
    )
    metadata["factor_risk_contribution"] = copy.deepcopy(audit["rows"])
    metadata["macro_factor_risk_decomposition"] = copy.deepcopy(audit)
    recommended["metadata"] = metadata
    allocations["recommended"] = recommended
    payload["allocations"] = allocations
    payload["macro_factor_risk_audit"] = {
        **audit,
        "version": "5.1.3",
        "weights_changed": False,
        "backtest_values_changed": False,
    }
    payload.pop("model_hash", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    payload["model_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


__all__ = [
    "attach_factor_risk_audit_v51",
    "macro_factor_risk_decomposition_v51",
]
