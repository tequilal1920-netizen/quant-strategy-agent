from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import asset_allocation_visual_v522 as visual
import model_governance_backend as governance
import research_evidence_backend as evidence
from asset_allocation_contract_v522 import (
    DISPLAY_STRATEGY_ID,
    DISPLAY_STRATEGY_ROLE,
    equal_weight_display_benchmark_v522,
)


ASSETS = ("equity", "bond", "gold", "commodity")
CYCLES = ("pring", "kitchin", "juglar", "merrill", "kondratieff")
POLICY_WEIGHTS = {
    "equity": 0.60,
    "bond": 0.15,
    "gold": 0.10,
    "commodity": 0.15,
}
CORE_MODEL_STEPS = {"输入", "计算", "约束", "输出", "最终作用"}


def _metrics(seed: float) -> dict[str, dict[str, float | int]]:
    return {
        split: {
            "months": 12,
            "annual_return": seed + offset,
            "annual_excess_return": seed / 10 + offset,
            "annual_volatility": 0.08 + offset,
            "sharpe": 0.50 + seed + offset,
            "information_ratio": 0.20 + seed + offset,
            "max_drawdown": -0.03 - offset,
        }
        for split, offset in (
            ("train", 0.01),
            ("validation", 0.02),
            ("test", 0.03),
        )
    }


def _nav(final_value: float) -> list[dict[str, float | str]]:
    return [
        {"month": "202601", "nav": 1.0},
        {"month": "202602", "nav": final_value},
    ]


def _returns() -> list[dict[str, float | str]]:
    return [
        {
            "month": "202601",
            "net_return": 0.01,
            "linear_cost": 0.00006,
            "quadratic_cost": 0.00004,
            "cost": 0.00010,
        },
        {
            "month": "202602",
            "net_return": 0.02,
            "linear_cost": 0.00003,
            "quadratic_cost": 0.00002,
            "cost": 0.00005,
        },
    ]


def _references() -> dict:
    def report(
        title: str,
        scope: str = "exact_method",
        status: str = "inspected",
    ) -> dict[str, str]:
        return {
            "broker": "测试券商",
            "title": title,
            "date": "2026-07-01",
            "url": f"https://example.invalid/{title}",
            "verification_status": status,
            "scope": scope,
        }

    return {
        "schema_version": "1.0",
        "cycle_models": {
            "pring": {
                "authoritative_references": [report("普林格专属方法")]
            },
            "kitchin": {
                "authoritative_references": [
                    *[report(f"基钦合格研报{i}") for i in range(1, 6)],
                    report("基钦第六篇应截断", "cross_cycle_framework"),
                    report("基钦未核验", status="documented"),
                    report("基钦错误范围", scope="market_commentary"),
                ]
            },
            "juglar": {
                "authoritative_references": [
                    report("朱格拉未核验", status="documented")
                ]
            },
            "merrill": {
                "authoritative_references": [
                    report("美林跨周期框架", "cross_cycle_framework")
                ]
            },
            "kondratieff": {"authoritative_references": []},
        },
        "allocation_models": {
            "black_litterman": {
                "authoritative_references": [
                    {
                        **report("AI赋能资产配置（三十四）：封面报告题名"),
                        "report_date": "2026-07-01",
                        "cataloged_at": "2026-07-02",
                        "matched_section": "AI视角驱动的Black-Litterman资产配置",
                    }
                ]
            },
            "risk_parity": {
                "authoritative_references": [
                    report("风险平价未核验", status="documented")
                ]
            },
            "risk_budget": {
                "authoritative_references": [
                    report("风险预算跨周期框架", "cross_cycle_framework")
                ]
            },
            "macro_factor_model": {
                "authoritative_references": [report("宏观因子风险模型方法")]
            },
        },
    }


@pytest.fixture()
def snapshot() -> dict:
    factor_registry = []
    cycle_states = {}
    availability = {}
    history = []
    contributions = {}
    view_labels = ["equity_vs_bond", "commodity_vs_bond", "gold_vs_bond"]
    for index, cycle in enumerate(CYCLES, start=1):
        factor_key = f"{cycle}_primary_factor"
        factor_registry.append(
            {
                "cycle": cycle,
                "pillar": f"{cycle}_pillar",
                "factor_key": factor_key,
                "economic_role": f"{cycle}_economic_role",
                "frequency": "monthly",
                "required_for_admission": True,
                "accepted_fields": [f"WIND_{cycle.upper()}"],
            }
        )
        eligible = cycle == "pring"
        data_status = (
            "D2_execution_proxy_shadow_only_not_D3"
            if cycle == "pring"
            else "display_only_insufficient_independent_cycles"
            if cycle == "kondratieff"
            else "pit_or_vintage_not_verified"
        )
        cycle_states[cycle] = {
            "state": f"state_{cycle}",
            "state_name": f"stage_{cycle}",
            "probabilities": {"early": 0.30, "late": 0.70},
            "confidence": 0.70,
            "method": f"explicit_duration_filter_{cycle}",
            "duration_model": {
                "minimum_months": index,
                "expected_months": index + 2,
                "maximum_months": index + 4,
                "method": "semi_markov",
            },
            "data_status": data_status,
            "eligible_for_views": eligible,
            "eligible_for_shadow_views": eligible,
            "eligible_for_production_views": False,
            "view_scope": "shadow_only" if eligible else "not_admitted",
            "factor_evidence": {
                "source": "Wind SQL Server",
                "observed_fields": (
                    {factor_key: f"WIND_{cycle.upper()}"}
                    if cycle == "pring"
                    else {}
                ),
            },
        }
        availability[cycle] = {
            "data_status": cycle_states[cycle]["data_status"],
            "eligible_for_views": eligible,
            "eligible_for_shadow_views": eligible,
            "eligible_for_production_views": False,
            "view_scope": "shadow_only" if eligible else "not_admitted",
            "observed_fields": (
                {factor_key: f"WIND_{cycle.upper()}"}
                if cycle == "pring"
                else {}
            ),
        }
        contributions[cycle] = [
            index / 100.0,
            (index + 1) / 100.0,
            (index + 2) / 100.0,
        ]

    for month, high in (("202601", 0.65), ("202602", 0.70)):
        history.append(
            {
                "month": month,
                "cycles": {
                    cycle: {
                        "state": f"state_{cycle}",
                        "state_name": f"stage_{cycle}",
                        "probabilities": {"early": 1.0 - high, "late": high},
                        "confidence": high,
                    }
                    for cycle in CYCLES
                },
            }
        )

    strength = {
        "equity": {
            "strength_rank": 1,
            "strength_label_cn": "最强",
            "composite_strength": 0.80,
            "active_weight": 0.06,
            "decision_summary_cn": "增长与风险偏好共同支持超配",
            "input_signals": [{"name": "growth", "value": 0.8}],
            "strength_tied_assets": ["equity"],
        },
        "commodity": {
            "strength_rank": 2,
            "strength_label_cn": "偏强",
            "composite_strength": 0.40,
            "active_weight": 0.01,
            "decision_summary_cn": "通胀与库存信号支持略超配",
            "input_signals": [{"name": "inflation", "value": 0.4}],
            "strength_tied_assets": ["commodity"],
        },
        "bond": {
            "strength_rank": 3,
            "strength_label_cn": "并列最弱",
            "composite_strength": -0.50,
            "active_weight": -0.04,
            "decision_summary_cn": "增长阶段压制久期资产",
            "input_signals": [{"name": "duration", "value": -0.5}],
            "strength_tied_assets": ["bond", "gold"],
        },
        "gold": {
            "strength_rank": 3,
            "strength_label_cn": "并列最弱",
            "composite_strength": -0.50,
            "active_weight": -0.03,
            "decision_summary_cn": "真实利率抵消避险需求",
            "input_signals": [{"name": "real_rate", "value": -0.5}],
            "strength_tied_assets": ["bond", "gold"],
        },
    }
    absolute_strength = {
        "commodity": {
            "strength_rank": 1,
            "strength_label_cn": "最强",
            "composite_strength": 0.70,
            "active_weight": None,
            "decision_summary_cn": "商品为无基准版最强收益信号",
            "input_signals": [{"name": "cycle_view", "value": 0.7}],
            "strength_tied_assets": ["commodity"],
        },
        "equity": {
            "strength_rank": 2,
            "strength_label_cn": "偏强",
            "composite_strength": 0.30,
            "active_weight": None,
            "decision_summary_cn": "权益信号偏强但权重仍受完整协方差约束",
            "input_signals": [{"name": "trend", "value": 0.3}],
            "strength_tied_assets": ["equity"],
        },
        "bond": {
            "strength_rank": 3,
            "strength_label_cn": "偏弱",
            "composite_strength": 0.00,
            "active_weight": None,
            "decision_summary_cn": "国债信号偏弱但承担低波动风险预算",
            "input_signals": [{"name": "duration", "value": 0.0}],
            "strength_tied_assets": ["bond"],
        },
        "gold": {
            "strength_rank": 4,
            "strength_label_cn": "最弱",
            "composite_strength": -0.80,
            "active_weight": None,
            "decision_summary_cn": "黄金为无基准版最弱收益信号",
            "input_signals": [{"name": "real_rate", "value": -0.8}],
            "strength_tied_assets": ["gold"],
        },
    }
    relative_weights = {
        "equity": 0.66,
        "bond": 0.11,
        "gold": 0.07,
        "commodity": 0.16,
    }
    absolute_weights = {
        "equity": 0.35,
        "bond": 0.30,
        "gold": 0.15,
        "commodity": 0.20,
    }
    equal = {
        "id": DISPLAY_STRATEGY_ID,
        "role": DISPLAY_STRATEGY_ROLE,
        "optimizer_input": False,
        "active_return_reference": False,
        "current_weights": [0.25, 0.25, 0.25, 0.25],
        "weights": [
            {
                "month": month,
                "equity": 0.25,
                "bond": 0.25,
                "gold": 0.25,
                "commodity": 0.25,
            }
            for month in ("202601", "202602")
        ],
        "nav": _nav(1.33),
        "returns": _returns(),
        # Sentinels prove these fields never leak into formal metrics/cost/risk views.
        "metrics": _metrics(999.0),
        "active_metrics": {"sentinel": "EQUAL_ACTIVE_SENTINEL"},
        "cost_audit": {"sentinel": "EQUAL_COST_SENTINEL"},
        "risk_contribution": {asset: 999.0 for asset in ASSETS},
    }
    return {
        "schema_version": "5.2.2",
        "engine_version": "asset-allocation-v5.2.2-user-approved-sharpe-mandate",
        "status": "ready",
        "data_as_of": "2026-02-28",
        "asset_order": list(ASSETS),
        "asset_labels": {asset: asset for asset in ASSETS},
        "benchmark": {
            "id": "strategic_60_15_10_15",
            "weights": dict(POLICY_WEIGHTS),
        },
        "cycle_factor_registry": factor_registry,
        "cycle_factor_availability": {
            "admitted_cycles": ["pring"],
            "production_admitted_cycles": [],
            "cycles": availability,
        },
        "cycle_history": history,
        "allocations": {
            "current_cycle": {
                "source": "Wind SQL Server",
                "cycles": cycle_states,
            },
            "strategic_benchmark": {
                "weights": dict(POLICY_WEIGHTS),
                "risk_contribution": dict(POLICY_WEIGHTS),
            },
            "risk_parity": {
                "weights": {asset: 0.25 for asset in ASSETS},
                "risk_contribution": {asset: 0.25 for asset in ASSETS},
                "metadata": {
                    "status": "solved",
                    "diagnostics": {"maximum_budget_error": 1e-10},
                },
            },
            "macro_risk_budget": {
                "weights": {
                    "equity": 0.40,
                    "bond": 0.25,
                    "gold": 0.15,
                    "commodity": 0.20,
                },
                "risk_contribution": {
                    "equity": 0.30,
                    "bond": 0.25,
                    "gold": 0.20,
                    "commodity": 0.25,
                },
                "metadata": {
                    "status": "solved",
                    "active_constraints": ["long_only", "turnover_cap"],
                },
            },
            "robust_bl": {
                "weights": dict(absolute_weights),
                "risk_contribution": {
                    "equity": 0.40,
                    "bond": 0.20,
                    "gold": 0.15,
                    "commodity": 0.25,
                },
                "metadata": {"model_version": "absolute_no_benchmark"},
            },
            "benchmark_relative": {
                "weights": relative_weights,
                "risk_contribution": {
                    "equity": 0.45,
                    "bond": 0.15,
                    "gold": 0.10,
                    "commodity": 0.30,
                },
                # These are deliberate obsolete-level traps.
                "cycle_views": {
                    "view_labels": ["TRAP"],
                    "cycle_contributions": {
                        cycle: [0.99] for cycle in CYCLES
                    },
                },
                "black_litterman": {"q": [99.0] * 9},
                "asset_strength": {
                    "rows": {asset: {"strength_rank": 99} for asset in ASSETS}
                },
                "metadata": {
                    "model_version": "benchmark_relative",
                    "cycle_views": {
                        "view_labels": view_labels,
                        "cycle_contributions": contributions,
                    },
                    "risk_budget": {"status": "solved"},
                    "black_litterman": {
                        "q": [0.01, 0.02],
                        "posterior_mean": [0.0101, 0.0202, 0.0303, 0.0404],
                        "diagnostics": {"status": "solved"},
                    },
                    "optimizer": {
                        "objective_terms": {"expected_return": 0.012345},
                        "constraint_slack": {"max_violation": 1e-9},
                    },
                    "asset_strength": {"rows": strength},
                },
            },
            "absolute_no_benchmark": {
                "weights": absolute_weights,
                "metadata": {
                    "model_version": "absolute_no_benchmark",
                    "asset_strength": {"rows": absolute_strength},
                },
            },
        },
        "asset_decisions": {
            "benchmark_relative": copy.deepcopy(strength),
            "absolute_no_benchmark": copy.deepcopy(absolute_strength),
        },
        "current_strength_summary": {
            "benchmark_relative": {
                "strongest_asset": "equity",
                "strongest_assets": ["equity"],
                "weakest_asset": None,
                "weakest_assets": ["bond", "gold"],
            },
            "absolute_no_benchmark": {
                "strongest_asset": "commodity",
                "strongest_assets": ["commodity"],
                "weakest_asset": "gold",
                "weakest_assets": ["gold"],
            },
        },
        "macro_factor_risk_audit": {
            "by_model_version": {
                "benchmark_relative": {
                    "status": "solved",
                    "factor_names": ["growth", "inflation", "liquidity"],
                    "factor_exposure": {"growth": 0.2, "inflation": -0.1},
                    "macro_blend_weight": 0.5,
                    "formula": "Sigma=alpha*BFB'+(1-alpha)*Sigma_stat",
                    "production_interpretation": "PIT-gated covariance blend",
                }
            }
        },
        "backtest": {
            "strategies": {
                "strategic_benchmark": {
                    "nav": _nav(9.99),
                    "returns": _returns(),
                    "metrics": _metrics(0.00),
                },
                "benchmark_relative": {
                    "nav": _nav(1.11),
                    "returns": _returns(),
                    "metrics": _metrics(0.10),
                },
                "absolute_no_benchmark": {
                    "nav": _nav(1.22),
                    "returns": _returns(),
                    "metrics": _metrics(0.20),
                },
                DISPLAY_STRATEGY_ID: equal,
            }
        },
        "quality": {
            "status": "passed",
            "promotion_gate": {"status": "passed"},
            "statistical_evidence_gate": {"status": "warning"},
            "statistical_evidence_by_version": {
                "benchmark_relative": {"status": "warning"}
            },
        },
        "deployment_decision": {
            "status": "user_approved_sharpe_mandate",
            "deployable_dynamic_model": True,
            "executed_mode": "benchmark_relative",
            "authorization_basis": "explicit_user_approval_sharpe_only",
        },
        "methodology": {"test_policy": "retrospective_report_only"},
        "cost_consistency_audit": {"status": "passed"},
        "model_evidence_catalog": _references(),
    }


def _table_rows(block: dict) -> list[dict]:
    return block["table"]["rows"]


def test_formal_four_panel_cycle_model_and_metadata_contract(snapshot: dict) -> None:
    blocks = visual.build(snapshot, page="strategy")
    assert list(blocks) == ["descriptive", "history", "diagnostics", "strategy"]

    factor_rows = [
        row
        for row in _table_rows(blocks["descriptive"])
        if row.get("row_type", "factor") == "factor"
    ]
    assert {row["cycle"] for row in factor_rows} == set(
        visual.CYCLE_LABELS.values()
    )
    for row in factor_rows:
        if row["cycle"] == visual.CYCLE_LABELS["kondratieff"]:
            assert row["source"] == "--（无可用独立长周期样本）"
            assert row["asset_mapping"] == "未准入，门禁强制零贡献"
        else:
            assert row["source"] == "Wind SQL Server"
        assert row["pit_status"]
        assert row["data_status"]
        assert row["view_scope"] in {"shadow_only", "not_admitted"}
        assert row["stage"]
        assert row["asset_mapping"] not in (None, "", "--")
        assert row["enters_allocation"]
    # Correct metadata-layer contributions win over the obsolete top-level trap.
    pring_mapping = next(
        row["asset_mapping"]
        for row in factor_rows
        if row["cycle"] == visual.CYCLE_LABELS["pring"]
    )
    assert "+1.00%" in pring_mapping
    assert "+99.00%" not in pring_mapping
    pring_factor = next(
        row
        for row in factor_rows
        if row["cycle"] == visual.CYCLE_LABELS["pring"]
    )
    assert pring_factor["pit_status"] == "D2影子准入（非D3/非生产）"
    assert pring_factor["enters_allocation"] == "仅影子研究（非D3/非生产）"
    assert pring_factor["enters_shadow_allocation"] is True
    assert pring_factor["enters_production_allocation"] is False
    assert all(
        row["enters_allocation"] == "否（零贡献）"
        and row["enters_shadow_allocation"] is False
        and row["enters_production_allocation"] is False
        for row in factor_rows
        if row["cycle"] != visual.CYCLE_LABELS["pring"]
    )

    history_rows = _table_rows(blocks["history"])
    assert len(history_rows) == 5
    assert {row["cycle"] for row in history_rows} == set(
        visual.CYCLE_LABELS.values()
    )
    for row in history_rows:
        assert row["current_stage"].startswith("stage_")
        assert row["probability_distribution"] not in (None, "", "--")
        assert row["judgment_method"].startswith("explicit_duration_filter_")
        assert row["duration"] not in (None, "", "--")
        assert row["asset_mapping"] not in (None, "", "--")
        assert row["enters_allocation"]
    pring_history = next(
        row
        for row in history_rows
        if row["cycle"] == visual.CYCLE_LABELS["pring"]
    )
    assert pring_history["view_scope"] == "shadow_only"
    assert pring_history["enters_shadow_allocation"] is True
    assert pring_history["enters_production_allocation"] is False
    assert pring_history["enters_allocation"] == "仅影子研究（非D3/非生产）"
    assert "仅按等概率作研究展示" in blocks["history"]["note"]
    history_traces = blocks["history"]["chart"]["traces"]
    assert len(history_traces) == 5
    assert {trace["name"] for trace in history_traces} == set(
        visual.CYCLE_LABELS.values()
    )
    assert all(len(trace["x"]) == len(trace["y"]) == 2 for trace in history_traces)

    diagnostic_rows = _table_rows(blocks["diagnostics"])
    core_rows = [
        row
        for row in diagnostic_rows
        if row.get("row_type", "model_step") == "model_step"
    ]
    model_order = list(dict.fromkeys(row["model"] for row in core_rows))
    assert len(model_order) == 4
    for model in model_order:
        rows = [row for row in core_rows if row["model"] == model]
        assert len(rows) == 5
        assert {row["step"] for row in rows} == CORE_MODEL_STEPS
        assert all(row["evidence"] not in (None, "", "--") for row in rows)

    bl_rows = [row for row in core_rows if row["model"] == model_order[3]]
    assert "2" in next(row["evidence"] for row in bl_rows if row["step"] == "输入")
    assert "+0.0101" in next(
        row["evidence"] for row in bl_rows if row["step"] == "输出"
    )
    assert "99" not in next(row["evidence"] for row in bl_rows if row["step"] == "输入")

    erc_effect = next(
        row["evidence"]
        for row in core_rows
        if row["model"] == model_order[1] and row["step"] == "最终作用"
    )
    assert "独立等风险诊断" in erc_effect
    assert "不直接改变推荐权重" in erc_effect
    budget_input = next(
        row["evidence"]
        for row in core_rows
        if row["model"] == model_order[2] and row["step"] == "输入"
    )
    assert "已准入周期目标风险预算" in budget_input
    assert "战略等风险基线" not in budget_input

    diagnostic_traces = blocks["diagnostics"]["chart"]["traces"]
    assert [trace["name"] for trace in diagnostic_traces] == [
        "严格ERC（独立诊断）",
        "相对版约束风险预算",
        "无基准版BL后成本优化组合",
        "相对版BL后成本优化组合",
    ]
    assert [trace["source_allocation_key"] for trace in diagnostic_traces] == [
        "risk_parity",
        "macro_risk_budget",
        "robust_bl",
        "benchmark_relative",
    ]
    assert [trace["model_version"] for trace in diagnostic_traces] == [
        "diagnostic_only",
        "benchmark_relative",
        "absolute_no_benchmark",
        "benchmark_relative",
    ]
    assert all(trace["diagnostic_role"] for trace in diagnostic_traces)


def test_strategy_chart_equal_weight_is_strictly_display_only(snapshot: dict) -> None:
    strategy = visual.build(snapshot)["strategy"]
    traces = strategy["chart"]["traces"]
    assert len(traces) == 3
    assert {trace["y"][-1] for trace in traces} == {1.11, 1.22, 1.33}
    assert all(trace["y"][-1] != 9.99 for trace in traces)

    rows = _table_rows(strategy)
    assert len(rows) == 20
    assert all(row.get("model_version") for row in rows)
    current_rows = [row for row in rows if row["kind"] == "当前权重"]
    metric_rows = [row for row in rows if row["kind"] == "分段绩效"]
    strength_rows = [row for row in rows if row["kind"] == "资产强弱"]
    assert len(current_rows) == 3
    assert len(metric_rows) == 9
    assert len(strength_rows) == 8
    assert {row["model_version"] for row in strength_rows} == {
        "benchmark_relative",
        "absolute_no_benchmark",
    }
    assert {
        version: len(
            [row for row in strength_rows if row["model_version"] == version]
        )
        for version in ("benchmark_relative", "absolute_no_benchmark")
    } == {"benchmark_relative": 4, "absolute_no_benchmark": 4}
    assert {row["model_version"] for row in current_rows} == {
        "strategic_benchmark",
        "benchmark_relative",
        "absolute_no_benchmark",
    }
    assert {
        version: len(
            [row for row in metric_rows if row["model_version"] == version]
        )
        for version in (
            "strategic_benchmark",
            "benchmark_relative",
            "absolute_no_benchmark",
        )
    } == {
        "strategic_benchmark": 3,
        "benchmark_relative": 3,
        "absolute_no_benchmark": 3,
    }
    assert {row["split"] for row in metric_rows} == {
        "train",
        "validation",
        "test",
    }
    assert sorted(row["annual_return"] for row in metric_rows) == pytest.approx(
        [0.01, 0.02, 0.03, 0.11, 0.12, 0.13, 0.21, 0.22, 0.23]
    )
    policy_row = next(
        row
        for row in current_rows
        if all(row[asset] == POLICY_WEIGHTS[asset] for asset in ASSETS)
    )
    assert {asset: policy_row[asset] for asset in ASSETS} == POLICY_WEIGHTS

    relative_strength = {
        row["asset"]: row
        for row in strength_rows
        if row["model_version"] == "benchmark_relative"
    }
    assert {asset: relative_strength[asset]["strength_rank"] for asset in ASSETS} == {
        "equity": 1,
        "commodity": 2,
        "bond": 3,
        "gold": 3,
    }
    assert relative_strength["equity"]["strength_label"] == "最强"
    assert relative_strength["commodity"]["strength_label"] == "偏强"
    assert relative_strength["bond"]["strength_label"] == "并列最弱"
    assert relative_strength["gold"]["strength_label"] == "并列最弱"
    assert relative_strength["bond"]["tie_assets"] == "bond,gold"
    assert relative_strength["gold"]["tie_assets"] == "bond,gold"

    absolute_strength = {
        row["asset"]: row
        for row in strength_rows
        if row["model_version"] == "absolute_no_benchmark"
    }
    assert {asset: absolute_strength[asset]["strength_rank"] for asset in ASSETS} == {
        "commodity": 1,
        "equity": 2,
        "bond": 3,
        "gold": 4,
    }
    assert absolute_strength["commodity"]["strength_label"] == "最强"
    assert absolute_strength["gold"]["strength_label"] == "最弱"
    assert {row["portfolio"] for row in relative_strength.values()} == {
        "基准高低配版"
    }
    assert {row["portfolio"] for row in absolute_strength.values()} == {
        "无基准版"
    }
    assert all(
        row["decision_summary"] != "--" and row["input_signals"] != "--"
        for row in strength_rows
    )

    serialized_table = json.dumps(rows, ensure_ascii=False)
    assert DISPLAY_STRATEGY_ID not in serialized_table
    assert "999" not in serialized_table
    assert "EQUAL_ACTIVE_SENTINEL" not in serialized_table
    assert "EQUAL_COST_SENTINEL" not in serialized_table
    assert DISPLAY_STRATEGY_ID not in snapshot["allocations"]
    assert DISPLAY_STRATEGY_ID not in snapshot["asset_decisions"]
    assert snapshot["benchmark"]["weights"] == POLICY_WEIGHTS


def test_formal_research_and_governance_api_contract(snapshot: dict) -> None:
    payload = evidence._allocation_v522(snapshot, "strategy")
    assert payload["status"] == "user_approved_sharpe_mandate"
    assert payload["champion"]["executed_mode"] == "benchmark_relative"
    assert payload["champion"]["weights"] == snapshot["allocations"][
        "benchmark_relative"
    ]["weights"]
    assert list(payload["visuals"]) == [
        "descriptive",
        "history",
        "diagnostics",
        "strategy",
    ]
    assert len(payload["cycle_status"]) == 5
    assert len(payload["model_chain"]) == 4
    assert {row["split"] for row in payload["metrics"]} == {
        "train",
        "validation",
        "test",
    }
    assert {row["split"] for row in payload["absolute_metrics"]} == {
        "train",
        "validation",
        "test",
    }
    assert payload["governance"]["policy_benchmark"] == {
        "id": "strategic_60_15_10_15",
        "weights": POLICY_WEIGHTS,
        "role": "optimizer_and_active_return_anchor",
    }
    display = payload["governance"]["display_benchmark"]
    assert display == {
        "id": DISPLAY_STRATEGY_ID,
        "strategy_key": DISPLAY_STRATEGY_ID,
        "weights": {asset: 0.25 for asset in ASSETS},
        "role": DISPLAY_STRATEGY_ROLE,
        "optimizer_input": False,
        "active_return_reference": False,
        "nav_observations": 2,
        "return_observations": 2,
    }
    assert "999" not in json.dumps(payload, ensure_ascii=False)
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) < 100_000

    model = {"engine": "unset"}
    governance._update_asset_v522(model, snapshot)
    assert model["engine"] == snapshot["engine_version"]
    assert model["champion"] == "benchmark_relative 基准高低配版"
    assert set(model["splits"]) == {"train", "validation", "test"}
    assert len(model["cycle_status"]) == 5
    assert len(model["model_chain"]) == 4
    assert model["robustness"]["policy_benchmark"] == POLICY_WEIGHTS
    assert model["robustness"]["nav_display_only_benchmark"] == display


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda data: data["backtest"]["strategies"].pop(
                DISPLAY_STRATEGY_ID
            ),
            "backtest.strategies.equal_weight_25_must_be_an_object",
        ),
        (
            lambda data: data["backtest"]["strategies"][DISPLAY_STRATEGY_ID][
                "returns"
            ][0].__setitem__("quadratic_cost", 0.00005),
            "returns.0.cost_must_equal_linear_cost_plus_quadratic_cost",
        ),
    ],
)
def test_formal_backends_fail_closed_on_invalid_equal_weight_contract(
    snapshot: dict, mutation, reason: str
) -> None:
    invalid = copy.deepcopy(snapshot)
    mutation(invalid)
    message = f"v522_equal_weight_contract_invalid:{reason}"
    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        equal_weight_display_benchmark_v522(invalid)
    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        evidence._allocation_v522(invalid, "strategy")
    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        governance._update_asset_v522({"engine": "unset"}, invalid)


def test_authoritative_references_are_filtered_capped_and_fallbacked(
    snapshot: dict,
) -> None:
    blocks = visual.build(snapshot)
    factor_rows = _table_rows(blocks["descriptive"])
    cycle_refs = [
        row
        for row in factor_rows
        if row.get("row_type") in {"authoritative_reference", "reference_fallback"}
    ]
    assert cycle_refs
    accepted = [
        row for row in cycle_refs if row["row_type"] == "authoritative_reference"
    ]
    assert all(row["verification_status"] == "inspected" for row in accepted)
    assert all(
        row["reference_scope"] in {"exact_method", "cross_cycle_framework"}
        for row in accepted
    )
    assert len(
        [row for row in accepted if row["reference_model_id"] == "kitchin"]
    ) == 5
    cycle_text = json.dumps(cycle_refs, ensure_ascii=False)
    assert "基钦第六篇应截断" not in cycle_text
    assert "基钦未核验" not in cycle_text
    assert "基钦错误范围" not in cycle_text
    for cycle in ("juglar", "kondratieff"):
        fallback = [
            row
            for row in cycle_refs
            if row["reference_model_id"] == cycle
            and row["row_type"] == "reference_fallback"
        ]
        assert len(fallback) == 1
        assert fallback[0]["factor"] == "暂无已核验专属研报"

    diagnostic_rows = _table_rows(blocks["diagnostics"])
    allocation_refs = [
        row
        for row in diagnostic_rows
        if row.get("row_type") in {"authoritative_reference", "reference_fallback"}
    ]
    assert allocation_refs
    accepted = [
        row
        for row in allocation_refs
        if row["row_type"] == "authoritative_reference"
    ]
    assert all(row["verification_status"] == "inspected" for row in accepted)
    assert all(
        row["reference_scope"] in {"exact_method", "cross_cycle_framework"}
        for row in accepted
    )
    bl_reference = next(
        row
        for row in accepted
        if row["reference_model_id"] == "black_litterman"
    )
    assert bl_reference["reference_title"] == "AI赋能资产配置（三十四）：封面报告题名"
    assert bl_reference["reference_report_date"] == "2026-07-01"
    assert bl_reference["reference_cataloged_at"] == "2026-07-02"
    assert (
        bl_reference["reference_matched_section"]
        == "AI视角驱动的Black-Litterman资产配置"
    )
    assert "对应章节=AI视角驱动的Black-Litterman资产配置" in bl_reference[
        "evidence"
    ]
    assert "目录收录=2026-07-02" in bl_reference["evidence"]
    fallback = [
        row
        for row in allocation_refs
        if row["reference_model_id"] == "risk_parity"
        and row["row_type"] == "reference_fallback"
    ]
    assert len(fallback) == 1
    assert fallback[0]["evidence"] == "暂无已核验专属研报"
    serialized = json.dumps(allocation_refs, ensure_ascii=False)
    assert "风险平价未核验" not in serialized
