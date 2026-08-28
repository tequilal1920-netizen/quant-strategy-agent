from __future__ import annotations

import numpy as np
import pytest

from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v549_direct_stack import allocate_relative_legacy_direct_v549


def _months():
    return [f"{year:04d}{month:02d}" for year in (2018, 2019, 2020) for month in range(1, 13)]


def _returns():
    return np.random.default_rng(5491).normal([.006,.003,.004,.005],[.04,.012,.03,.04],size=(36,4))


def test_direct_stack_has_complete_kkt_and_no_other_inference():
    returns=_returns(); parameters=StackParametersV53(macro_blend_weight=0.0, risk_aversion=4.0, active_risk_aversion=4.0, active_l2_penalty=.01)
    result=allocate_relative_legacy_direct_v549(returns,np.zeros_like(returns),np.zeros_like(returns,dtype=bool),_months(),{"cycles":{}},[.60,.15,.10,.15],parameters)
    weights=np.asarray(result["weights"]); optimizer=result["optimizer"]
    assert result["signal_path"] == "direct_active_alpha"
    assert result["other_inference"]["used"] is False
    assert result["posterior_uncertainty_penalty"] == 0.0
    assert optimizer["objective_terms"]["posterior_uncertainty_penalty"] == pytest.approx(0.0)
    assert optimizer["status"] == "optimal"
    assert optimizer["solver"]["maximum_kkt_residual"] <= 1e-7
    assert optimizer["solver"]["fallback_used"] is False
    assert result["post_solve_scaling_applied"] is False
    assert abs(float(weights.sum())-1.0)<=1e-9
    assert .5*float(np.abs(weights-np.asarray([.60,.15,.10,.15])).sum())<=.10+1e-8
    assert optimizer["constraints"]["annual_tracking_error"]<=.08+1e-8
    assert optimizer["constraints"]["one_way_turnover"]<=.08+1e-8
    assert result["direct_alpha_formula_max_residual"]<=1e-12
    assert result["signal_window_start"]=="201801" and result["signal_window_end"]=="202012"


def test_direct_stack_macro_false_cells_and_future_are_inert():
    returns=_returns(); macro=np.zeros_like(returns); admission=np.zeros_like(returns,dtype=bool); p=StackParametersV53(macro_blend_weight=0.0,risk_aversion=4.0,active_risk_aversion=4.0)
    base=allocate_relative_legacy_direct_v549(returns,macro,admission,_months(),{"cycles":{}},[.60,.15,.10,.15],p)
    changed=macro.copy(); changed[:]=1e9
    counter=allocate_relative_legacy_direct_v549(returns,changed,admission,_months(),{"cycles":{}},[.60,.15,.10,.15],p)
    assert np.allclose(base["weights"],counter["weights"],atol=1e-12)


def test_direct_stack_rejects_noncontiguous_calendar_and_production_cycle():
    months=_months(); months[0]="201712"; r=_returns(); z=np.zeros_like(r); p=StackParametersV53(macro_blend_weight=0.0)
    with pytest.raises(ValueError,match="contiguous"):
        allocate_relative_legacy_direct_v549(r,z,z.astype(bool),months,{"cycles":{}},[.60,.15,.10,.15],p)
    cycle={"cycles":{"pring":{"eligible_for_production_views":True,"view_scope":"production","data_status":"D3_verified"}}}
    with pytest.raises(RuntimeError,match="not_certified"):
        allocate_relative_legacy_direct_v549(r,z,z.astype(bool),_months(),cycle,[.60,.15,.10,.15],p)