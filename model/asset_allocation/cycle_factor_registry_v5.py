"""Governed canonical factor schema for the v5 economic-cycle models.

This registry defines economic meaning, transformations and admission rules;
it intentionally does *not* invent Wind/iFind/RiceQuant series identifiers.
Provider codes must be bound and sample-verified by the data connector layer
before a row can carry ``_pit_verified=True``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SOURCE_PRIORITY_V5 = ("Wind", "iFind", "RiceQuant", "official_public_crosscheck")


@dataclass(frozen=True)
class CycleFactorSpecV5:
    cycle: str
    pillar: str
    factor_key: str
    accepted_fields: tuple[str, ...]
    transform: str
    direction: float
    required_for_admission: bool
    minimum_history_months: int
    economic_role: str
    frequency: str = "monthly"
    pit_required: bool = True

    def validate(self) -> None:
        if self.cycle not in {"kitchin", "juglar", "merrill"}:
            raise ValueError(f"unsupported_cycle_factor:{self.cycle}")
        if not self.pillar or not self.factor_key or not self.accepted_fields:
            raise ValueError("cycle_factor_identity_missing")
        if self.transform not in {
            "level",
            "yoy_or_level_yoy",
            "spread",
            "inverse_level",
            "momentum_3m",
        }:
            raise ValueError(f"unsupported_cycle_factor_transform:{self.transform}")
        if self.direction not in {-1.0, 1.0}:
            raise ValueError("cycle_factor_direction_must_be_signed_unit")
        if self.minimum_history_months < 12:
            raise ValueError("cycle_factor_history_requirement_too_short")


CYCLE_FACTOR_REGISTRY_V5: tuple[CycleFactorSpecV5, ...] = (
    # Kitchin: admission requires a real inventory observation and demand;
    # PPI is not an admissible substitute for physical/financial inventory.
    CycleFactorSpecV5(
        "kitchin", "inventory", "real_inventory_growth",
        ("industrial_finished_goods_inventory_yoy", "industrial_finished_goods_inventory"),
        "yoy_or_level_yoy", 1.0, True, 36,
        "工业企业产成品存货同比；定义库存方向，严禁用PPI替代",
    ),
    CycleFactorSpecV5(
        "kitchin", "demand", "industrial_revenue_growth",
        ("industrial_revenue_yoy", "industrial_revenue"),
        "yoy_or_level_yoy", 1.0, True, 36,
        "工业企业营业收入/主营业务收入同比，刻画已实现需求",
    ),
    CycleFactorSpecV5(
        "kitchin", "demand", "pmi_new_orders",
        ("pmi_new_orders",), "level", 1.0, False, 24,
        "制造业PMI新订单，提供需求领先确认",
    ),
    # Juglar: all four economic pillars are mandatory for D3 admission.
    CycleFactorSpecV5(
        "juglar", "investment", "manufacturing_investment_growth",
        ("manufacturing_fai_yoy", "manufacturing_fai"),
        "yoy_or_level_yoy", 1.0, True, 48,
        "制造业固定资产投资同比，度量资本开支",
    ),
    CycleFactorSpecV5(
        "juglar", "credit", "enterprise_medium_long_credit",
        ("enterprise_medium_long_loan_yoy", "enterprise_medium_long_loan"),
        "yoy_or_level_yoy", 1.0, True, 48,
        "企业中长期贷款，度量资本开支融资条件",
    ),
    CycleFactorSpecV5(
        "juglar", "capacity", "industrial_capacity_utilization",
        ("capacity_utilization",), "level", 1.0, True, 36,
        "工业产能利用率，确认设备能力利用阶段",
        frequency="quarterly_month_end_carry_forward",
    ),
    CycleFactorSpecV5(
        "juglar", "profit", "industrial_profit_growth",
        ("industrial_profit_yoy", "industrial_profit_total"),
        "yoy_or_level_yoy", 1.0, True, 48,
        "规模以上工业企业利润同比，确认投资回报与出清",
    ),
    # Merrill China extension: growth/inflation form the clock, while credit,
    # liquidity and valuation+risk appetite are independent mandatory axes.
    CycleFactorSpecV5(
        "merrill", "growth", "manufacturing_growth",
        ("pmi_manufacturing",), "level", 1.0, True, 24,
        "制造业PMI增长水平",
    ),
    CycleFactorSpecV5(
        "merrill", "growth", "industrial_activity_growth",
        ("industrial_value_added_yoy", "pmi_composite"),
        "level", 1.0, False, 24,
        "工业增加值或综合PMI增长确认",
    ),
    CycleFactorSpecV5(
        "merrill", "inflation", "consumer_inflation",
        ("cpi_national_yoy",), "level", 1.0, True, 24,
        "CPI同比",
    ),
    CycleFactorSpecV5(
        "merrill", "inflation", "producer_inflation",
        ("ppi_yoy",), "level", 1.0, True, 24,
        "PPI同比，补充上游价格方向",
    ),
    CycleFactorSpecV5(
        "merrill", "credit", "social_financing_impulse",
        ("sf_stock_yoy", "sf_stock_endval"),
        "yoy_or_level_yoy", 1.0, True, 36,
        "社会融资规模存量同比/信用脉冲",
    ),
    CycleFactorSpecV5(
        "merrill", "credit", "m1_m2_spread",
        ("m1_m2_spread",), "spread", 1.0, False, 24,
        "M1-M2剪刀差，提供企业资金活化确认",
    ),
    CycleFactorSpecV5(
        "merrill", "liquidity", "broad_money_liquidity",
        ("m2_yoy",), "level", 1.0, True, 24,
        "M2同比，度量广义流动性",
    ),
    CycleFactorSpecV5(
        "merrill", "liquidity", "interbank_liquidity",
        ("dr007", "shibor_3m"), "inverse_level", 1.0, False, 24,
        "DR007或3个月Shibor的反向标准分，确认银行间流动性",
    ),
    CycleFactorSpecV5(
        "merrill", "valuation", "equity_valuation_support",
        ("equity_risk_premium", "equity_valuation_percentile"),
        "level", 1.0, True, 36,
        "股权风险溢价优先；估值分位需反向后表示估值支撑",
    ),
    CycleFactorSpecV5(
        "merrill", "risk_appetite", "stock_bond_risk_appetite",
        ("stock_bond_relative_momentum",), "momentum_3m", 1.0, True, 24,
        "股债相对强弱，度量可交易风险偏好而非主观打分",
    ),
)


def validate_cycle_factor_registry_v5() -> dict[str, Any]:
    identities: set[tuple[str, str]] = set()
    pillars: dict[str, set[str]] = {"kitchin": set(), "juglar": set(), "merrill": set()}
    for specification in CYCLE_FACTOR_REGISTRY_V5:
        specification.validate()
        identity = (specification.cycle, specification.factor_key)
        if identity in identities:
            raise ValueError(f"duplicate_cycle_factor:{identity}")
        identities.add(identity)
        if specification.required_for_admission:
            pillars[specification.cycle].add(specification.pillar)
    required = {
        "kitchin": {"inventory", "demand"},
        "juglar": {"investment", "credit", "capacity", "profit"},
        "merrill": {"growth", "inflation", "credit", "liquidity", "valuation", "risk_appetite"},
    }
    missing = {
        cycle: sorted(expected - pillars[cycle])
        for cycle, expected in required.items()
        if expected - pillars[cycle]
    }
    if missing:
        raise ValueError(f"cycle_factor_registry_missing_required_pillars:{missing}")
    return {
        "status": "passed",
        "factor_count": len(CYCLE_FACTOR_REGISTRY_V5),
        "required_pillars": {cycle: sorted(values) for cycle, values in required.items()},
        "source_priority": list(SOURCE_PRIORITY_V5),
        "provider_binding_policy": "connector must bind verified provider field/code and PIT metadata; this schema never guesses identifiers",
    }


def serialise_cycle_factor_registry_v5() -> list[dict[str, Any]]:
    validate_cycle_factor_registry_v5()
    return [asdict(specification) for specification in CYCLE_FACTOR_REGISTRY_V5]


__all__ = [
    "CYCLE_FACTOR_REGISTRY_V5",
    "CycleFactorSpecV5",
    "SOURCE_PRIORITY_V5",
    "serialise_cycle_factor_registry_v5",
    "validate_cycle_factor_registry_v5",
]
