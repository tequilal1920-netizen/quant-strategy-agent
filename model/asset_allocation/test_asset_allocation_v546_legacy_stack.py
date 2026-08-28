from __future__ import annotations

import numpy as np

from asset_allocation_v53_stack import StackParametersV53
from asset_allocation_v546_legacy_stack import allocate_relative_legacy_v546


def test_legacy_stack_is_direct_active_and_fully_certified():
    rng = np.random.default_rng(5461)
    returns = rng.normal([.006, .003, .004, .005], [.04, .012, .03, .04], size=(36, 4))
    months = [f"{year:04d}{month:02d}" for year in (2018, 2019, 2020) for month in range(1, 13)]
    result = allocate_relative_legacy_v546(
        returns,
        np.zeros_like(returns),
        np.zeros_like(returns, dtype=bool),
        months,
        {"cycles": {}},
        [.60, .15, .10, .15],
        StackParametersV53(macro_blend_weight=0.0),
    )
    weights = np.asarray(result["weights"])
    assert result["optimizer"]["status"] == "optimal"
    assert result["optimizer"]["solver"]["maximum_kkt_residual"] <= 1.0e-7
    assert result["post_solve_scaling_applied"] is False
    assert result["challenger_family"] == "legacy_B06_fixed_mechanism_transfer"
    assert abs(float(weights.sum()) - 1.0) <= 1.0e-9
    assert .5 * float(np.abs(weights - np.asarray([.60, .15, .10, .15])).sum()) <= .10 + 1.0e-8
