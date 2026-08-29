"""Build v5.9 three-asset equal-anchor asset-allocation snapshot.

v5.9 keeps the user's latest contract:

* asset universe: equity, bond, commodity only; gold remains removed;
* benchmark / optimizer anchor / active-return reference: 1/3 each;
* required model families remain visible: BL, risk parity, all-weather, macro factor;
* adds an explicit equal-anchor active-rotation tracker as the research champion.

The new active-rotation tracker is deliberately simple and auditable: 3/6/12
month risk-adjusted cross-asset trend, monthly rebalancing, active-share and
turnover caps, same transaction-cost model, and no macro/cycle data unless the
D3/PIT gates are satisfied.  It is visible as a research-service model, not a
production-promoted model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_MODEL_ROOT = PROJECT_ROOT / "model" / "asset_allocation"
if str(ASSET_MODEL_ROOT) not in sys.path:
    sys.path.insert(0, str(ASSET_MODEL_ROOT))

from backtest_asset_allocation_v541_long import _drift  # noqa: E402
from build_snapshot_v58_equal_anchor_no_gold_asset_block import (  # noqa: E402
    ASSET_LABELS,
    ASSET_ORDER,
    DEFAULT_OUTPUT,
    DISPLAY_EQUAL,
    EXPECTED_PANEL_HASH,
    PANEL_PATH,
    POLICY,
    _cost,
    _fixed_rows,
    _hash,
    _latest_weights,
    _read,
    _select_three_asset_returns,
    _strategy_payload,
    _validate_panel,
    build_snapshot as _build_v58_snapshot,
)


SCHEMA_V59 = "5.9.0"
ENGINE_V59 = "asset-allocation-v59-three-asset-equal-anchor-model-zoo"
AUDIT_OUTPUT = PROJECT_ROOT / "output" / "model_improvement" / "asset_allocation_snapshot_v59_equal_anchor_model_zoo_research.json"

ACTIVE_ROTATION_SPEC: dict[str, Any] = {
    "id": "V59-ACTIVE-ROTATION-3_6_12-TILT-035",
    "family": "equal_anchor_active_rotation_tracker",
    "horizons": [[3, 0.30], [6, 0.40], [12, 0.30]],
    "profile": "risk_adjusted_rank_tilt",
    "tilt_scale": 0.35,
    "lookback_months": 36,
    "volatility_window_months": 24,
    "min_weight": 0.05,
    "max_weight": 0.85,
    "max_active_share": 0.55,
    "max_one_way_turnover": 0.25,
    "rebalance_frequency": "monthly",
    "benchmark_anchor": "equal_weight_1_3_each",
    "selection_protocol": (
        "single pre-registered long-horizon challenger; 2022+ remains report-only "
        "and is not allowed to change the formula"
    ),
}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest().upper()


def _average_rank_desc(score: np.ndarray) -> np.ndarray:
    order = np.argsort(-score)
    ranks = np.empty(len(score), dtype=float)
    index = 0
    while index < len(score):
        end = index
        while end + 1 < len(score) and abs(float(score[order[end + 1]] - score[order[index]])) < 1.0e-12:
            end += 1
        average = (index + end) / 2.0
        for position in range(index, end + 1):
            ranks[order[position]] = average
        index = end + 1
    return ranks


def _bounded_simplex(weights: np.ndarray, lo: float, hi: float) -> np.ndarray:
    out = np.asarray(weights, dtype=float).copy()
    if not np.all(np.isfinite(out)):
        raise ValueError("v59_active_rotation_non_finite_weight")
    for _ in range(100):
        before = out.copy()
        out = np.clip(out, lo, hi)
        diff = 1.0 - float(out.sum())
        free = (out > lo + 1.0e-12) & (out < hi - 1.0e-12)
        if not np.any(free):
            out = out / out.sum()
            out = np.clip(out, lo, hi)
            out = out / out.sum()
            break
        out[free] += diff / float(free.sum())
        if np.max(np.abs(out - before)) < 1.0e-12 and abs(float(out.sum()) - 1.0) < 1.0e-12:
            break
    out = np.clip(out, lo, hi)
    out = out / out.sum()
    return out


def _active_rotation_target(window: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    if window.shape[0] < int(ACTIVE_ROTATION_SPEC["lookback_months"]):
        raise ValueError("v59_active_rotation_requires_36_month_window")
    volatility = np.maximum(
        window[-int(ACTIVE_ROTATION_SPEC["volatility_window_months"]) :].std(axis=0, ddof=1) * math.sqrt(12.0),
        0.02,
    )
    score = np.zeros(len(ASSET_ORDER), dtype=float)
    horizon_details: list[dict[str, Any]] = []
    for horizon, weight in ACTIVE_ROTATION_SPEC["horizons"]:
        h = int(horizon)
        w = float(weight)
        horizon_return = np.prod(1.0 + window[-h:], axis=0) - 1.0
        adjusted = horizon_return / np.maximum(volatility * math.sqrt(h / 12.0), 0.02)
        score += w * adjusted
        horizon_details.append(
            {
                "horizon_months": h,
                "weight": w,
                "compound_return": {asset: float(horizon_return[i]) for i, asset in enumerate(ASSET_ORDER)},
                "risk_adjusted_score": {asset: float(adjusted[i]) for i, asset in enumerate(ASSET_ORDER)},
            }
        )
    centered = score - float(score.mean())
    denominator = max(float(np.abs(centered).sum()), 1.0e-12)
    raw = POLICY + float(ACTIVE_ROTATION_SPEC["tilt_scale"]) * centered / denominator * 2.0
    bounded = _bounded_simplex(
        raw,
        float(ACTIVE_ROTATION_SPEC["min_weight"]),
        float(ACTIVE_ROTATION_SPEC["max_weight"]),
    )
    rank = _average_rank_desc(score)
    diagnostics = {
        "score": {asset: float(score[i]) for i, asset in enumerate(ASSET_ORDER)},
        "rank": {asset: float(rank[i]) for i, asset in enumerate(ASSET_ORDER)},
        "volatility": {asset: float(volatility[i]) for i, asset in enumerate(ASSET_ORDER)},
        "raw_pre_bound": {asset: float(raw[i]) for i, asset in enumerate(ASSET_ORDER)},
        "target_before_turnover": {asset: float(bounded[i]) for i, asset in enumerate(ASSET_ORDER)},
        "horizon_details": horizon_details,
    }
    return bounded, diagnostics


def _active_rotation_rows(months: Sequence[str], returns: np.ndarray) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous = POLICY.copy()
    rows: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for signal_index in range(35, len(returns) - 1):
        window = returns[signal_index - 35 : signal_index + 1]
        target, diagnostics = _active_rotation_target(window)

        active = 0.5 * float(np.abs(target - POLICY).sum())
        if active > float(ACTIVE_ROTATION_SPEC["max_active_share"]):
            target = POLICY + (target - POLICY) * (float(ACTIVE_ROTATION_SPEC["max_active_share"]) / active)
            target = target / target.sum()
        turnover = 0.5 * float(np.abs(target - previous).sum())
        if turnover > float(ACTIVE_ROTATION_SPEC["max_one_way_turnover"]):
            target = previous + (target - previous) * (float(ACTIVE_ROTATION_SPEC["max_one_way_turnover"]) / turnover)
            target = target / target.sum()

        realised = returns[signal_index + 1]
        change = target - previous
        row_cost = _cost(change)
        rows.append(
            {
                "signal_month": str(months[signal_index]),
                "month": str(months[signal_index + 1]),
                "net_return": float(target @ realised) - row_cost,
                "turnover": 0.5 * float(np.abs(change).sum()),
                "cost": row_cost,
                "weights": target.tolist(),
                "score": diagnostics["score"],
                "rank": diagnostics["rank"],
            }
        )
        last = {
            "weights": target.tolist(),
            "signal_diagnostics": diagnostics,
            "active_share": 0.5 * float(np.abs(target - POLICY).sum()),
            "turnover_from_drifted_previous": 0.5 * float(np.abs(change).sum()),
            "strongest_asset": max(diagnostics["score"], key=diagnostics["score"].get),
            "weakest_asset": min(diagnostics["score"], key=diagnostics["score"].get),
        }
        previous = _drift(target, realised)
    return rows, last



def _enhanced_cycle_factor_rows() -> list[dict[str, Any]]:
    rows = [
        ("??", "??-????", "??????????????????????", "Wind/iFinD/RQ/???????", "40-60?", "display_only", "???????PIT", "????", "?????????????????"),
        ("??", "??-??", "????????????????????", "Wind/iFinD/???????", "40-60?", "display_only", "?????D3???", "????", "????????"),
        ("???", "??", "???????????????????", "Wind??/iFinD??", "?/?", "display_only", "release-vintage PIT??", "????", "?????????????"),
        ("???", "??", "???????????????????", "Wind??/iFinD??", "?", "display_only", "available_time/vintage???", "????", "??????????????"),
        ("???", "??", "???????ROE??????", "Wind/iFinD??", "?/?", "display_only", "????PIT???", "????", "??????????????"),
        ("??", "??", "???????????????????/???", "Wind/iFinD??", "?", "display_only", "release-vintage PIT??", "????", "???????/?????PIT????"),
        ("??", "??", "PMI?????????????-???", "Wind/iFinD/?????", "?", "display_only", "?????????", "????", "????????????????"),
        ("??", "??", "PPI?CRB/?????????????", "Wind/iFinD/RQ", "?", "display_only", "??PIT???", "????", "??????????????"),
        ("??", "??", "PMI????????????", "Wind/iFinD??", "?", "display_only", "????PIT???", "????", "??????????????"),
        ("??", "??", "CPI?PPI???CPI???????", "Wind/iFinD??", "?", "display_only", "????/???????", "????", "????????????"),
        ("??", "??", "???M1-M2????????????", "Wind/iFinD??", "?", "display_only", "??PIT???", "????", "????????????"),
        ("??", "???", "DR007/SHIBOR????????????", "Wind/RQ??", "?/?", "display_only", "?????????????PIT???", "????", "??????????????"),
        ("??", "??????", "??????ERP????????????", "Wind/iFinD??", "?/?", "display_only", "?????D3", "????", "?????????????"),
        ("???", "????", "????/???????????", "RQ D2?Wind D3???", "?", "shadow_only", "D2??????D3??", "????", "???????????/??"),
        ("???", "????", "??300???????????????", "RQ D2?Wind D3???", "?", "shadow_only", "D2??????D3??", "????", "?????????????"),
        ("???", "????", "?????????????????/????", "RQ+??D2?Wind/iFinD???", "?", "shadow_only", "???????D3????", "????", "???????????"),
        ("???", "?????", "???????????????", "????D2", "?", "shadow_only", "?????????", "????", "???????????/????"),
    ]
    return [
        {
            "cycle": cycle,
            "pillar": pillar,
            "factor": factor,
            "source": source,
            "frequency": frequency,
            "view_scope": scope,
            "data_status": status,
            "enters_allocation": enters,
            "current_stage": stage,
        }
        for cycle, pillar, factor, source, frequency, scope, status, enters, stage in rows
    ]


def build_snapshot() -> dict[str, Any]:
    panel = _read(PANEL_PATH)
    _validate_panel(panel)
    months, returns = _select_three_asset_returns(panel)
    base = _build_v58_snapshot()

    equal_rows = _fixed_rows(months, returns, DISPLAY_EQUAL)
    active_rows, active_last = _active_rotation_rows(months, returns)
    current_weights = _latest_weights(active_rows, active_last.get("weights") or POLICY)
    active_model = _strategy_payload(
        "active_rotation",
        "等权锚主动轮动（3/6/12月相对强弱）",
        active_rows,
        equal_rows,
        current_weights,
        role="三资产等权锚上的主动超额研究冠军：中期趋势确认 + 成本/换手/主动偏离约束",
        construction=[
            "资产只保留权益、国债、非黄金商品，黄金已从宇宙、权重、周期映射和可视化中删除",
            "基准、优化锚、主动收益参考全部为权益/国债/商品各1/3",
            "信号仅用信号月及以前36个月收益；3/6/12月风险调整相对强弱按30%/40%/30%合成",
            "横截面强弱只生成相对等权的主动倾斜；单资产权重5%-85%、主动偏离和月度换手均受控",
            "2022年以后只作为报告期展示；公式不因报告期表现继续调参",
        ],
        governance="research-only; validation-visible challenger; not D3 production promoted",
    )
    active_model["model_spec"] = ACTIVE_ROTATION_SPEC
    active_model["latest_signal"] = active_last
    active_model["selection_note"] = {
        "selection_uses_test": False,
        "report_only_period": "2022+",
        "why_added": "risk parity has high Sharpe but weak excess; active rotation explicitly targets excess versus the 1/3 benchmark",
        "candidate_spec_sha256": _canonical_hash(ACTIVE_ROTATION_SPEC),
    }

    models = dict(base["allocation_models"])
    models = {"active_rotation": active_model, **models}
    base["schema_version"] = SCHEMA_V59
    base["engine_version"] = ENGINE_V59
    base["allocation_models"] = models
    base["cycle_tracking"]["factor_rows"] = _enhanced_cycle_factor_rows()
    base["cycle_tracking"]["framework_completion"] = {
        "cycles": ["??", "???", "??", "??", "???"],
        "production_admitted_cycles": [],
        "shadow_admitted_cycles": ["???"],
        "reason": "?Wind/iFinD/RQ??release-vintage???D3?????????????????????????????",
    }
    base["recommended"] = {
        "primary_model": "active_rotation",
        "reason": (
            "v5.9在保留BL、风险平价、全天候、宏观因子的基础上新增等权锚主动轮动。"
            "它不是用测试期选出来的生产冠军，而是当前三资产等权锚下超额更清晰的研究推荐；"
            "风险平价仍作为Sharpe/回撤诊断冠军。"
        ),
        "sharpe_champion": "risk_parity",
        "excess_champion_vs_equal_display": "active_rotation",
        "current_cycle_aligned_model": "macro_factor",
        "current_asset_strength": active_last.get("signal_diagnostics", {}).get("score", {}),
    }
    base["model_zoo"] = {
        "added_in_v59": ["active_rotation"],
        "required_families_preserved": ["black_litterman", "risk_parity", "all_weather", "macro_factor"],
        "active_rotation_spec_sha256": _canonical_hash(ACTIVE_ROTATION_SPEC),
        "selection_uses_test": False,
        "deployment_allowed": False,
    }
    base["references"] = list(base.get("references") or []) + [
        {
            "name": "skfolio Benchmark tracking / walk-forward portfolio engineering",
            "url": "https://skfolio.org/",
            "usage": "v5.9把主动模型改为围绕1/3基准的benchmark-aware overlay，而不是绝对权重排序。",
        },
        {
            "name": "Riskfolio-Lib risk parity / risk budgeting",
            "url": "https://riskfolio-lib.readthedocs.io/",
            "usage": "保留ERC作为独立高Sharpe低回撤对照，不把它伪装成超额冠军。",
        },
    ]
    base["governance"]["selection_uses_test"] = False
    base["governance"]["deployment_allowed"] = False
    base["governance"]["truth_boundary"] = (
        "v5.9可作为网页研究服务部署；正式生产晋级仍需Wind/iFinD/RQ D3交叉验证和未来纯净shadow样本。"
    )
    base.pop("content_sha256", None)
    base["content_sha256"] = _hash(base)
    return base


def write_snapshot(output: Path) -> dict[str, Any]:
    snapshot = build_snapshot()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(output)
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    snapshot = write_snapshot(Path(args.output))
    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": snapshot["schema_version"],
                "content_sha256": snapshot["content_sha256"],
                "recommended": snapshot["recommended"],
                "active_rotation_full": snapshot["allocation_models"]["active_rotation"]["metrics"]["full"],
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
