from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = Path(__file__).with_name("catalog.json")


class QueryError(RuntimeError):
    """可向调用 Agent 直接展示的输入或数据错误。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QueryError(f"缺少数据文件：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise QueryError(f"数据文件不可读：{path}；{exc}") from exc
    if not isinstance(payload, dict):
        raise QueryError(f"数据文件顶层必须是对象：{path}")
    return payload


def catalog() -> dict[str, Any]:
    return _read_json(CATALOG_PATH)


def _snapshot_root() -> Path:
    configured = os.environ.get("QUANT_AGENT_SNAPSHOT_ROOT", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_dir():
            raise QueryError(f"QUANT_AGENT_SNAPSHOT_ROOT 不存在：{path}")
        return path
    candidates = [
        ROOT / "board" / "quant_strategy_agent_vnext" / "data",
        ROOT / "board" / "quant_strategy_agent" / "data",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    raise QueryError("没有找到模型快照目录，请设置 QUANT_AGENT_SNAPSHOT_ROOT")


def _snapshot(name: str) -> tuple[dict[str, Any], Path]:
    path = _snapshot_root() / name
    return _read_json(path), path


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _compact_metrics(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    keep = (
        "status",
        "start",
        "end",
        "observations",
        "annual_return",
        "benchmark_annual_return",
        "annual_excess",
        "sharpe",
        "excess_sharpe",
        "information_ratio",
        "max_drawdown",
        "annual_turnover",
        "turnover",
        "rank_ic",
        "icir",
        "hit_rate",
    )
    return {key: value.get(key) for key in keep if key in value}


def _response(
    skill: str,
    operation: str,
    payload: dict[str, Any],
    source: Path | str,
    result: Any,
) -> dict[str, Any]:
    return {
        "状态": "正常",
        "模型": skill,
        "动作": operation,
        "数据截止": payload.get("as_of")
        or payload.get("data_as_of")
        or payload.get("verified_at"),
        "生成时间": payload.get("generated_at")
        or payload.get("verified_at"),
        "数据来源": str(source),
        "结果": result,
    }


def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in params and params[name] not in (None, ""):
            return params[name]
    return default


def _limit(params: dict[str, Any], default: int = 10, maximum: int = 50) -> int:
    raw = _param(params, "limit", "数量", default=default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise QueryError("数量必须是整数") from exc
    return max(1, min(value, maximum))


def _asset_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    payload, path = _snapshot("asset_allocation_snapshot.json")
    allocations = payload.get("allocations") or {}
    current = allocations.get("current_cycle") or {}
    aliases = {
        "稳健": "conservative",
        "平衡": "balanced",
        "权益优先": "equity_preferred",
        "conservative": "conservative",
        "balanced": "balanced",
        "equity_preferred": "equity_preferred",
    }
    profile_raw = str(
        _param(
            params,
            "profile",
            "画像",
            default=allocations.get("default_profile") or "balanced",
        )
    )
    profile = aliases.get(profile_raw, profile_raw)
    profiles = allocations.get("profiles") or {}
    allocation = profiles.get(profile)
    if allocation is None and profile == allocations.get("default_profile"):
        allocation = allocations.get("recommended")
    if allocation is None:
        raise QueryError(f"未知资产画像：{profile_raw}；可选：稳健、平衡、权益优先")

    cycle = {
        "月份": current.get("month"),
        "普林格": current.get("pring_phase_name"),
        "普林格置信度": current.get("confidence"),
        "基钦": current.get("kitchin_state"),
        "朱格拉": current.get("juglar_state"),
        "康波": current.get("kondratieff_state"),
        "美林": current.get("merrill_state"),
        "增长得分": current.get("growth_score"),
        "通胀得分": current.get("inflation_score"),
        "流动性得分": current.get("liquidity_score"),
        "信用得分": current.get("credit_score"),
    }
    optimization = payload.get("optimization") or {}
    governance = {
        "入选方案": optimization.get("selected_spec"),
        "训练": _compact_metrics(optimization.get("train_metrics")),
        "验证": _compact_metrics(optimization.get("validation_metrics")),
        "测试仅报告": _compact_metrics(
            optimization.get("test_metrics_report_only")
        ),
        "多重试验修正": optimization.get("deflated_sharpe_probability"),
        "晋级门禁": optimization.get("promotion_gate"),
    }
    if operation == "cycle":
        result = {
            "周期": cycle,
            "状态概率": {
                "普林格": current.get("pring_probability"),
                "基钦": current.get("kitchin_probability"),
                "朱格拉": current.get("juglar_probability"),
                "康波": current.get("kondratieff_probability"),
                "美林": current.get("merrill_probability"),
            },
        }
    elif operation == "backtest":
        result = governance
    elif operation == "current":
        result = {
            "周期": cycle,
            "资产画像": profile,
            "资产权重": allocation.get("weights"),
            "风险贡献": allocation.get("risk_contribution"),
            "换手": (allocation.get("metadata") or {}).get(
                "current_rebalance_turnover"
            ),
            "治理": governance,
        }
    else:
        raise QueryError("资产配置动作仅支持 current、cycle、backtest")
    return _response("asset-allocation", operation, payload, path, result)


_INDUSTRY_DIMENSIONS = (
    ("prosperity", "景气度"),
    ("fundamental", "基本面"),
    ("technical", "技术面"),
    ("valuation", "估值"),
    ("funds", "资金面"),
    ("crowding", "拥挤度"),
)


def _industry_candidate_audit(
    block: dict[str, Any], candidate: str | None
) -> dict[str, Any] | None:
    if not candidate:
        return None
    audit = next(
        (
            row
            for row in (block.get("candidate_audit") or [])
            if isinstance(row, dict) and row.get("candidate") == candidate
        ),
        None,
    )
    if audit is None:
        return None

    def split_metrics(prefix: str) -> dict[str, Any]:
        return {
            key: audit.get(f"{prefix}_{key}")
            for key in (
                "annual_return",
                "sharpe",
                "excess_sharpe",
                "max_drawdown",
                "turnover",
            )
            if f"{prefix}_{key}" in audit
        }

    return {
        "候选": candidate,
        "名称": audit.get("candidate_label"),
        "架构": audit.get("architecture"),
        "定义": audit.get("definition"),
        "训练": split_metrics("train"),
        "验证": split_metrics("validation"),
        "验证年度超额夏普": audit.get("validation_yearly_excess_sharpe"),
        "测试仅报告": _compact_metrics(audit.get("report_only_test")),
        "训练验证目标": audit.get("objective"),
        "组合规则": audit.get("target_policy"),
    }


def _industry_dimension_row(row: dict[str, Any]) -> dict[str, Any]:
    components = row.get("components") or {}
    return {
        "排名": row.get("rank"),
        "行业": row.get("name") or row.get("industry"),
        "代码": row.get("code"),
        "六维综合得分": row.get("score"),
        "入选": row.get("selected"),
        "权重": row.get("weight"),
        "六维分解": {
            label: components.get(key) for key, label in _INDUSTRY_DIMENSIONS
        },
    }


def _industry_research_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "排名": row.get("rank"),
        "行业": row.get("name") or row.get("industry"),
        "代码": row.get("code"),
        "综合得分": row.get("score"),
        "入选": row.get("selected"),
        "权重": row.get("weight"),
        "信号日": row.get("signal_date"),
        "得分日期": row.get("score_date"),
    }


def _industry_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    payload, path = _snapshot("rotation_snapshot.json")
    research_payload = None
    research_path = None
    try:
        research_payload, research_path = _snapshot("industry_research_dashboard.json")
    except QueryError:
        research_payload, research_path = None, None
    limit = _limit(params)
    dimension_operations = {"dimensions", "six_dimension", "六维"}
    frequency_raw = str(
        _param(
            params,
            "frequency",
            "频率",
            default="monthly" if operation in dimension_operations else "high_frequency",
        )
    ).lower()
    aliases = {
        "高频": "high_frequency",
        "high": "high_frequency",
        "high_frequency": "high_frequency",
        "月频": "monthly",
        "月": "monthly",
        "monthly": "monthly",
        "周频": "weekly",
        "周": "weekly",
        "weekly": "weekly",
    }
    frequency = aliases.get(frequency_raw, frequency_raw)
    high_rows = (payload.get("high_frequency") or {}).get("industries") or []
    response_payload = payload
    if operation == "ranking":
        if frequency == "high_frequency":
            rows = sorted(high_rows, key=lambda row: row.get("rank") if row.get("rank") is not None else 10_000)
            ranking = [
                {
                    "排名": row.get("rank"),
                    "行业": row.get("industry"),
                    "代码": row.get("code"),
                    "景气得分": row.get("score"),
                    "入选": row.get("selected"),
                    "实时指标数": row.get("live_indicators"),
                    "数据质量": row.get("data_quality"),
                }
                for row in rows[:limit]
            ]
            metrics = None
        elif frequency in {"monthly", "weekly"}:
            block = (
                ((payload.get("industry") or {}).get("frequencies") or {}).get(
                    frequency
                )
                or {}
            )
            ranking = [
                {
                    "排名": row.get("rank"),
                    "行业": row.get("name"),
                    "代码": row.get("code"),
                    "得分": row.get("score"),
                    "入选": row.get("selected"),
                    "权重": row.get("weight"),
                }
                for row in (block.get("ranking") or [])[:limit]
            ]
            metrics = {
                name: _compact_metrics(value)
                for name, value in (block.get("metrics") or {}).items()
            }
        else:
            raise QueryError("行业频率仅支持 高频、月频、周频")
        result = {
            "频率": frequency,
            "模型口径": "生产冠军" if frequency != "high_frequency" else "高频景气",
            "排名": ranking,
            "绩效": metrics,
        }
    elif operation == "drivers":
        industry = str(_param(params, "industry", "行业", default="")).strip()
        if not industry:
            raise QueryError("drivers 动作必须提供 行业=行业名称")
        row = next(
            (
                item
                for item in high_rows
                if item.get("industry") == industry or item.get("code") == industry
            ),
            None,
        )
        if row is None:
            raise QueryError(f"未找到行业：{industry}")
        drivers = sorted(
            row.get("indicators") or [],
            key=lambda item: abs(_as_number(item.get("contribution")) or 0.0),
            reverse=True,
        )
        result = {
            "行业": row.get("industry"),
            "排名": row.get("rank"),
            "景气得分": row.get("score"),
            "驱动": [
                {
                    "指标": item.get("name"),
                    "频率": item.get("frequency"),
                    "单位": item.get("unit"),
                    "最新特征": item.get("latest_feature"),
                    "贡献": item.get("contribution"),
                    "方向": item.get("direction"),
                    "数据截止": item.get("last_available_date"),
                    "来源": item.get("source"),
                    "可用规则": item.get("availability_rule"),
                }
                for item in drivers[:limit]
            ],
        }
    elif operation in dimension_operations:
        if frequency not in {"monthly", "weekly"}:
            raise QueryError("六维动作的频率仅支持 月频、周频")
        block = (
            ((payload.get("industry") or {}).get("frequencies") or {}).get(
                frequency
            )
            or {}
        )
        model = block.get("six_dimension") or {}
        research_rotation = (research_payload or {}).get("rotation") or {}
        if frequency == "monthly" and research_rotation.get("ranking"):
            rows = sorted(
                (row for row in (research_rotation.get("ranking") or []) if isinstance(row, dict)),
                key=lambda row: (
                    row.get("rank") is None,
                    row.get("rank") if row.get("rank") is not None else 10_000,
                ),
            )
            industry = str(_param(params, "industry", "行业", default="")).strip()
            if industry:
                row = next(
                    (
                        item
                        for item in rows
                        if item.get("name") == industry
                        or item.get("industry") == industry
                        or item.get("code") == industry
                    ),
                    None,
                )
                if row is None:
                    raise QueryError(f"C45研究排名未找到行业：{industry}")
                ranking = _industry_research_row(row)
            else:
                ranking = [_industry_research_row(row) for row in rows[:limit]]
            result = {
                "频率": frequency,
                "数据截止": research_rotation.get("current_score_date") or (research_payload or {}).get("data_as_of"),
                "模型版本": research_rotation.get("model_id"),
                "生产冠军": block.get("selected_candidate"),
                "研究展示候选": research_rotation.get("model_id"),
                "研究角色": "训练验证筛选后的网页研究展示候选；测试集只报告",
                "打分逻辑": research_rotation.get("score_model"),
                "候选因子数": len(research_rotation.get("factor_table") or []),
                "高效因子数": len(research_rotation.get("efficient_factors") or []),
                "高效因子": [
                    {
                        "一级分类": row.get("dimension_label"),
                        "因子": row.get("factor_label"),
                        "筛选分": row.get("selection_score"),
                        "角色": row.get("selection_role"),
                    }
                    for row in (research_rotation.get("efficient_factors") or [])[:limit]
                ],
                "排名" if not industry else "行业分解": ranking,
                "绩效": {
                    split: _compact_metrics(metrics)
                    for split, metrics in (research_rotation.get("metrics") or {}).items()
                },
                "时点治理": {
                    "选择样本": "训练集与验证集",
                    "测试样本": "2022年后测试区间只报告，不参与候选排序或调参",
                    "执行规则": "月末信号，下一交易日执行，成本后回测",
                },
            }
            response_payload = {
                "as_of": result["数据截止"],
                "generated_at": (research_payload or {}).get("generated_at"),
            }
            return _response("industry-rotation", operation, response_payload, research_path or path, result)
        rows = model.get("research_ranking") or block.get("research_ranking") or []
        if not rows:
            raise QueryError(
                "当前行业快照未包含六维研究排序，请刷新到含 research_ranking 的快照；"
                "运行时不会用生产排名代替六维排名"
            )
        rows = sorted(
            (row for row in rows if isinstance(row, dict)),
            key=lambda row: (
                row.get("rank") is None,
                row.get("rank") if row.get("rank") is not None else 10_000,
            ),
        )
        industry = str(_param(params, "industry", "行业", default="")).strip()
        if industry:
            row = next(
                (
                    item
                    for item in rows
                    if item.get("name") == industry
                    or item.get("industry") == industry
                    or item.get("code") == industry
                ),
                None,
            )
            if row is None:
                raise QueryError(f"六维排序未找到行业：{industry}")
            ranking = [_industry_dimension_row(row)]
        else:
            ranking = [_industry_dimension_row(row) for row in rows[:limit]]

        root_model = payload.get("six_dimension") or {}
        factor_count = model.get("factor_count") or root_model.get("factor_count") or {}
        total_factors = sum(
            int(value)
            for value in factor_count.values()
            if _as_number(value) is not None
        )
        research_candidate = block.get("research_selected_candidate")
        promotion = block.get("promotion_gate") or {}
        result = {
            "频率": frequency,
            "数据截止": model.get("data_as_of") or root_model.get("data_as_of"),
            "模型版本": root_model.get("model_version"),
            "生产冠军": block.get("selected_candidate"),
            "研究挑战者": research_candidate,
            "研究角色": "post-test诊断研究挑战者",
            "因子总数": total_factors,
            "因子分布": factor_count,
            "维度角色": model.get("dimensions"),
            "排名" if not industry else "行业分解": ranking if not industry else ranking[0],
            "研究审计": _industry_candidate_audit(block, research_candidate),
            "晋级门禁": promotion,
            "时点治理": {
                "选择样本": (root_model.get("governance") or {}).get("selection"),
                "测试样本": (root_model.get("governance") or {}).get("test"),
                "晋级规则": (root_model.get("governance") or {}).get("promotion"),
                "标签规则": (root_model.get("diagnostics") or {}).get("horizon"),
                "计算规则": (root_model.get("diagnostics") or {}).get("method"),
                "数据质量": model.get("data_quality") or root_model.get("data_quality"),
            },
        }
        response_payload = {
            "as_of": result["数据截止"],
            "generated_at": payload.get("generated_at"),
        }
    elif operation == "style":
        style = payload.get("style") or {}
        cells = sorted(
            style.get("cells") or [],
            key=lambda item: _as_number(item.get("cap_share")) or 0.0,
            reverse=True,
        )
        result = {
            "信号日": style.get("latest_signal_date"),
            "执行日": style.get("latest_execution_date"),
            "频率": style.get("frequency"),
            "风格箱": cells[:limit],
            "迁移": style.get("migration"),
            "数据质量": style.get("data_quality"),
        }
    elif operation == "backtest":
        blocks = ((payload.get("industry") or {}).get("frequencies") or {})
        result = {
            name: {
                "入选方案": block.get("selected_candidate"),
                "研究挑战者": block.get("research_selected_candidate"),
                "绩效口径": "生产冠军",
                "绩效": {
                    split: _compact_metrics(metrics)
                    for split, metrics in (block.get("metrics") or {}).items()
                },
                "研究挑战者审计": _industry_candidate_audit(
                    block, block.get("research_selected_candidate")
                ),
                "晋级门禁": block.get("promotion_gate"),
                "测试使用": "仅报告或否决，不参与候选排序、选模或调参",
            }
            for name, block in blocks.items()
        }
        research_rotation = (research_payload or {}).get("rotation") or {}
        if research_rotation:
            result["月频研究展示C45"] = {
                "发布候选": research_rotation.get("model_id"),
                "打分逻辑": research_rotation.get("score_model"),
                "高效因子数": len(research_rotation.get("efficient_factors") or []),
                "绩效": {
                    split: _compact_metrics(metrics)
                    for split, metrics in (research_rotation.get("metrics") or {}).items()
                },
                "测试使用": "只报告，不参与候选排序、选模或调参",
            }
    else:
        raise QueryError(
            "行业景气度动作仅支持 ranking、drivers、dimensions、style、backtest"
        )
    return _response("industry-rotation", operation, response_payload, path, result)


def _latest_trace_point(trace: dict[str, Any]) -> dict[str, Any]:
    xs = trace.get("x") or []
    ys = trace.get("y") or []
    for index in range(min(len(xs), len(ys)) - 1, -1, -1):
        value = _as_number(ys[index])
        if value is not None:
            return {
                "名称": trace.get("name"),
                "日期": xs[index],
                "最新值": value,
                "来源编号": trace.get("source_id"),
            }
    return {
        "名称": trace.get("name"),
        "日期": None,
        "最新值": None,
        "来源编号": trace.get("source_id"),
    }


def _liquidity_page(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "标题": page.get("title"),
        "结论": page.get("conclusion"),
        "数据截止": page.get("as_of"),
        "图表": [
            {
                "图表": chart.get("title"),
                "频率": chart.get("frequency"),
                "参考": chart.get("reference"),
                "质量": chart.get("quality"),
                "最新值": [
                    _latest_trace_point(trace)
                    for trace in (chart.get("traces") or [])
                ],
            }
            for chart in (page.get("charts") or [])
        ],
    }


def _liquidity_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    payload, path = _snapshot("liquidity_snapshot.json")
    pages = payload.get("pages") or {}
    aliases = {
        "主页": "home",
        "散户": "retail",
        "公募": "public",
        "ETF": "etf",
        "etf": "etf",
        "融资": "margin",
        "一级市场": "primary",
        "私募": "private",
        "外资": "foreign",
    }
    if operation == "overview":
        result = {
            "数据质量": payload.get("quality"),
            "页面": {
                name: {
                    "标题": page.get("title"),
                    "结论": page.get("conclusion"),
                    "数据截止": page.get("as_of"),
                    "图表数": len(page.get("charts") or []),
                }
                for name, page in pages.items()
            },
        }
    elif operation == "page":
        raw = str(_param(params, "page", "页面", default="home"))
        name = aliases.get(raw, raw)
        if name not in pages:
            raise QueryError(
                "未知资金页面；可选：主页、散户、公募、ETF、融资、一级市场、私募、外资"
            )
        result = _liquidity_page(pages[name])
    else:
        raise QueryError("资金面动作仅支持 overview、page")
    return _response("liquidity-tracking", operation, payload, path, result)


def _factor_manifest() -> tuple[dict[str, Any], Path]:
    configured = os.environ.get("QUANT_AGENT_FACTOR_MANIFEST", "").strip()
    path = (
        Path(configured).expanduser()
        if configured
        else ROOT / "model" / "factor_laboratory" / "champion_manifest.json"
    )
    return _read_json(path), path


def _factor_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation == "champion":
        payload, path = _factor_manifest()
        result = {
            "冠军": payload.get("selected_candidate"),
            "选择依据": payload.get("selection_basis"),
            "测试用途": payload.get("test_usage"),
            "研究状态": payload.get("promotion_status"),
            "晋级结论": payload.get("promotion_decision"),
            "候选数": payload.get("candidate_count"),
            "三段绩效": payload.get("splits"),
            "门禁": payload.get("gates"),
            "候选归因": payload.get("candidate_diagnostics"),
        }
        return _response("factor-laboratory", operation, payload, path, result)

    payload, path = _snapshot("index_enhancement_snapshot.json")
    if operation == "index":
        universe = str(
            _param(params, "universe", "指数", default="CSI800_ENH")
        ).upper()
        audits = payload.get("champion_audit") or {}
        if universe not in audits:
            raise QueryError(f"未知指数增强标的池：{universe}")
        leaderboard = [
            row
            for row in (payload.get("leaderboard") or [])
            if row.get("universe") == universe
        ]
        result = {
            "标的池": universe,
            "冠军审计": audits.get(universe),
            "排行榜": leaderboard[: _limit(params)],
            "影子挑战者": payload.get("shadow_challenger_audit"),
            "治理": payload.get("governance"),
        }
    elif operation == "models":
        result = {
            "模型": payload.get("models"),
            "SmartBeta": payload.get("smartbeta"),
            "风险层": payload.get("risk"),
            "求解": payload.get("solver"),
            "治理": payload.get("governance"),
        }
    else:
        raise QueryError("因子实验室动作仅支持 champion、index、models")
    return _response("factor-laboratory", operation, payload, path, result)


def _technical_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation == "status":
        payload, path = _snapshot("kline_cross_sectional_audit.json")
        result = {
            "治理状态": payload.get("status"),
            "版本": payload.get("version"),
            "选择规则": payload.get("selection_policy"),
            "测试是否参与选择": payload.get("selection_uses_test"),
            "入选候选": payload.get("selected_candidate"),
            "候选数": payload.get("candidate_count"),
            "合格数": payload.get("eligible_count"),
            "切分": payload.get("split"),
            "完整性": payload.get("integrity"),
        }
        return _response("technical-analysis", operation, payload, path, result)
    if operation == "patterns":
        query_text = str(
            _param(params, "query", "关键词", default="")
        ).strip().lower()
        configured_root = os.environ.get("QUANT_AGENT_KLINE_PATTERN_ROOT", "").strip()
        root = (
            Path(configured_root).expanduser()
            if configured_root
            else ROOT / "skill" / "technical-analysis" / "references" / "kline-patterns"
        )
        rows = []
        for path in sorted(root.glob("*.md")):
            if query_text and query_text not in path.stem.lower():
                continue
            rows.append(
                {
                    "形态": path.stem,
                    "文件": str(path.relative_to(root)),
                }
            )
        payload = {"as_of": None, "generated_at": None}
        return _response(
            "technical-analysis",
            operation,
            payload,
            root,
            rows[: _limit(params, default=20, maximum=200)],
        )
    raise QueryError("技术分析动作仅支持 status、patterns")


def _portfolio_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    payload, path = _snapshot("portfolio_optimization_snapshot.json")
    home = payload.get("home") or {}
    optimization = payload.get("optimization") or {}
    backtest = payload.get("backtest") or {}
    if operation == "current":
        minimum = _as_number(
            _param(params, "minimum_weight", "最小权重", default=0.0001)
        )
        if minimum is None or minimum < 0:
            raise QueryError("最小权重必须是不小于0的数字")
        weights = [
            row
            for row in (home.get("current_weights") or [])
            if (_as_number(row.get("weight")) or 0.0) >= minimum
        ]
        result = {
            "候选": home.get("selected_candidate"),
            "求解器": home.get("selected_solver"),
            "权重": weights,
            "晋级门禁": home.get("promotion_gate"),
        }
    elif operation == "solver":
        result = {
            "入选参数": optimization.get("selected_spec"),
            "求解器基准": optimization.get("solver_benchmark"),
            "约束余量": optimization.get("constraint_slack"),
            "风险模型": (payload.get("risk_constraints") or {}).get(
                "risk_models"
            ),
            "约束": (payload.get("risk_constraints") or {}).get("constraints"),
            "多重试验修正": optimization.get("deflated_sharpe_probability"),
            "PBO": optimization.get("pbo_cscv"),
        }
    elif operation == "backtest":
        result = {
            "策略": backtest.get("strategies"),
            "收益损失归因": backtest.get("return_loss_attribution"),
            "成本敏感性": backtest.get("cost_sensitivity_test"),
            "压力情景": backtest.get("stress_scenarios"),
            "晋级门禁": backtest.get("promotion_gate"),
        }
    else:
        raise QueryError("组合优化动作仅支持 current、solver、backtest")
    return _response("portfolio-optimization", operation, payload, path, result)


def _dashboard_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation in {"overview", "market"}:
        payload, path = _snapshot("global_market_snapshot.json")
        rows = payload.get("rows") or []
        region = str(_param(params, "region", "地区", default="")).strip()
        if region:
            rows = [row for row in rows if str(row.get("region")) == region]
        ranked = sorted(
            rows,
            key=lambda row: abs(_as_number(row.get("ret_1d")) or 0.0),
            reverse=True,
        )
        result = {
            "市场": ranked[: _limit(params)],
            "状态": payload.get("status"),
        }
        if operation == "overview":
            root = _snapshot_root()
            result["快照"] = [
                {
                    "文件": item.name,
                    "大小": item.stat().st_size,
                    "更新时间": item.stat().st_mtime,
                }
                for item in sorted(root.glob("*.json"))
            ]
        return _response("data-dashboard", operation, payload, path, result)
    if operation == "news":
        payload, path = _snapshot("sina_news_snapshot.json")
        rows = (
            payload.get("rows")
            or payload.get("news")
            or payload.get("items")
            or []
        )
        result = rows[: _limit(params, default=20)]
        return _response("data-dashboard", operation, payload, path, result)
    raise QueryError("数据看板动作仅支持 overview、market、news")


def _research_home_query(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation != "overview":
        raise QueryError("主页动作仅支持 overview")
    modules: dict[str, Any] = {}
    calls = [
        ("数据看板", "data-dashboard", "market", {"数量": 5}),
        ("资产配置", "asset-allocation", "current", params),
        ("资金面", "liquidity-tracking", "overview", {}),
        ("行业景气", "industry-rotation", "ranking", {"数量": 5}),
        ("因子", "factor-laboratory", "champion", {}),
        ("技术分析", "technical-analysis", "status", {}),
        ("组合优化", "portfolio-optimization", "current", {"最小权重": 0.001}),
    ]
    for title, skill, action, child_params in calls:
        try:
            modules[title] = query(skill, action, child_params)
        except QueryError as exc:
            modules[title] = {"状态": "受阻", "原因": str(exc)}
    payload = {"as_of": None, "generated_at": None}
    return _response(
        "research-home",
        operation,
        payload,
        "七个一级研究模块",
        modules,
    )


def query(
    skill: str, operation: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    params = dict(params or {})
    dispatch = {
        "research-home": _research_home_query,
        "data-dashboard": _dashboard_query,
        "asset-allocation": _asset_query,
        "liquidity-tracking": _liquidity_query,
        "industry-rotation": _industry_query,
        "factor-laboratory": _factor_query,
        "technical-analysis": _technical_query,
        "portfolio-optimization": _portfolio_query,
    }
    handler = dispatch.get(skill)
    if handler is None:
        raise QueryError(
            f"未知模型 Skill：{skill}；运行 python -m agent_runtime catalog 查看清单"
        )
    return handler(operation, params)
